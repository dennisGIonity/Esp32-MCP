"""
REST + WebSocket surface.

/api/v1/telemetry          POST  HTTP ingest fallback (single or batch)
/api/v1/devices            GET   filtered device list
/api/v1/devices/{id}       GET   one device + history
/api/v1/devices/{id}/cmd   POST  command a device (or 'broadcast')
/api/v1/fleet/summary      GET   rollup
/api/v1/telemetry/query    GET   time-series query
/api/v1/alerts             GET   alerts
/api/v1/mcp/rpc            POST  MCP JSON-RPC over HTTP
/api/v1/devices/{id}/mcp   POST  relay JSON-RPC to the board's own MCP server (fw >= 2.0)
/api/v1/firmware/manifest  GET   flasher images (firmware/build.py)
/api/v1/firmware/{file}    GET   one merged image
/api/v1/provisioning/defaults GET what the flasher pre-fills (host, ports, token for admins)
/api/v1/integrations       GET   Datadog forwarder + MQTT bridge stats
/api/v1/health             GET   liveness + ingest stats
/ws/fleet                  WS    live dashboard feed
"""
from __future__ import annotations

import asyncio
import hmac
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, HTTPException, Header, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import Response, FileResponse

from app.config import settings
from app.models import TelemetryIn, CommandIn
from app.mcp import protocol as mcp_protocol
from app.mcp.tools import MCP_TOOLS

router = APIRouter()
ws_clients: set[WebSocket] = set()


def _check_token(token: str | None) -> None:
    if settings.require_token and not hmac.compare_digest(token or "", settings.fleet_token):
        raise HTTPException(status_code=401, detail="invalid or missing X-Fleet-Token")


async def _json_body(request: Request):
    """Parse the JSON body or answer 400 - never let a malformed byte stream
    surface as a 500 (the board bridges send hand-rolled JSON)."""
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="empty body, JSON expected")
    try:
        return json.loads(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"malformed JSON: {e}") from None


def _is_admin(request: Request) -> bool:
    """True when no admin token is configured, or the caller presented it as
    'Authorization: Bearer <t>' or 'X-Ionity-Token: <t>'."""
    want = settings.admin_token
    if not want:
        return True
    auth = request.headers.get("authorization", "")
    got = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-ionity-token", "")
    return hmac.compare_digest(got or "", want)


# --------------------------------------------------------------------------
# Ingest (HTTP fallback path - MQTT is primary)
# --------------------------------------------------------------------------
@router.post("/api/v1/telemetry")
async def ingest(request: Request, x_fleet_token: str | None = Header(default=None)):
    _check_token(x_fleet_token)
    body = await _json_body(request)
    registry = request.app.state.registry

    items = body if isinstance(body, list) else [body]
    if len(items) > 500:
        raise HTTPException(status_code=413, detail="max 500 readings per batch")

    accepted = 0
    for raw in items:
        try:
            t = TelemetryIn(**raw)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        # It arrived over HTTP, whatever the device believed - EXCEPT readings a
        # bridge forwarded on a device's behalf ("serial"), or the simulator's.
        # Overwriting those would hide how the reading actually travelled.
        if t.net.transport not in ("serial", "sim"):
            t.net.transport = "http"
        registry.ingest(t)
        accepted += 1

    return {"ok": True, "accepted": accepted, "queue_depth": registry.queue.qsize()}


@router.post("/api/v1/devices/register")
async def register(request: Request, x_fleet_token: str | None = Header(default=None)):
    """Optional first-boot handshake. Returns the effective config a node
    should adopt, so a batch can be re-tagged centrally."""
    _check_token(x_fleet_token)
    body = await _json_body(request)
    return {
        "ok": True,
        "device_id": body.get("device_id"),
        "mqtt": {"host": settings.mqtt_host, "port": settings.mqtt_port,
                 "root": settings.mqtt_root},
        "telemetry_interval_ms": 10000,
        "server_time": time.time(),
    }


# --------------------------------------------------------------------------
# Fleet reads
# --------------------------------------------------------------------------
@router.get("/api/v1/fleet/summary")
async def fleet_summary(request: Request):
    return request.app.state.registry.summary().model_dump()


@router.get("/api/v1/devices")
async def list_devices(
    request: Request,
    site: str | None = None, group: str | None = None,
    health: str | None = None, search: str | None = None,
    limit: int = Query(200, le=2000), offset: int = 0,
):
    views = request.app.state.registry.list_views(
        site=site, group=group, health=health, search=search,
        limit=limit, offset=offset,
    )
    return {"count": len(views), "devices": [v.model_dump() for v in views]}


@router.get("/api/v1/devices/{device_id}")
async def get_device(request: Request, device_id: str, history: int = Query(100, le=2000)):
    registry = request.app.state.registry
    view = registry.device_view(device_id)
    if not view:
        raise HTTPException(status_code=404, detail="unknown device")
    rows = await request.app.state.store.query_telemetry(device_id=device_id, limit=history)
    return {"device": view.model_dump(), "history": rows}


@router.post("/api/v1/devices/{device_id}/cmd")
async def device_command(request: Request, device_id: str, cmd: CommandIn,
                         x_fleet_token: str | None = Header(default=None)):
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="admin token required (Authorization: Bearer ...)")
    payload = {k: v for k, v in cmd.model_dump(by_alias=True).items()
               if k != "action" and v is not None}
    if cmd.action == "set_wifi":
        if not payload.get("ssid"):
            raise HTTPException(status_code=422, detail="set_wifi needs ssid")
        payload.setdefault("pass", "")
    return await request.app.state.registry.send_command(device_id, cmd.action, payload)


@router.delete("/api/v1/devices/{device_id}")
async def forget_device(request: Request, device_id: str):
    """Forget a device: live registry + every stored row (telemetry, alerts,
    command log). Admin-gated when a token is configured. Used by PURGE-DEMO
    and the smoke test so no server restart is needed to drop an emulator."""
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="admin token required (Authorization: Bearer ...)")
    res = await request.app.state.registry.forget_device(device_id)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail=res.get("error", "unknown device"))
    return res


@router.get("/api/v1/commands/results")
async def command_results(request: Request, device_id: str | None = None,
                          cmd_id: str | None = None, limit: int = Query(20, le=200)):
    return {"results": request.app.state.registry.list_cmd_results(
        device_id=device_id, cmd_id=cmd_id, limit=limit)}


@router.get("/api/v1/telemetry/query")
async def query_telemetry(
    request: Request, metric: str | None = None, device_id: str | None = None,
    site: str | None = None, group: str | None = None,
    minutes: int = 60, limit: int = Query(500, le=20000),
):
    since = time.time() - minutes * 60
    rows = await request.app.state.store.query_telemetry(
        device_id=device_id, site=site, group=group, metric=metric,
        since_s=since, limit=limit,
    )
    return {"metric": metric, "minutes": minutes, "points": len(rows), "rows": rows}


@router.get("/api/v1/telemetry/aggregate")
async def aggregate(request: Request, metric: str, minutes: int = 60,
                    site: str | None = None, group: str | None = None):
    since = time.time() - minutes * 60
    return await request.app.state.store.aggregate_metric(metric, since, site=site, group=group)


@router.get("/api/v1/alerts")
async def alerts(request: Request, device_id: str | None = None,
                 open_only: bool = True, limit: int = Query(100, le=500)):
    return {"alerts": await request.app.state.store.list_alerts(
        device_id=device_id, open_only=open_only, limit=limit)}


# --------------------------------------------------------------------------
# LAN DNS visibility
# --------------------------------------------------------------------------
@router.get("/api/v1/dns/summary")
async def dns_summary(request: Request, minutes: int = 60):
    store = request.app.state.store
    dns = getattr(request.app.state, "dns", None)
    out = await store.dns_summary(time.time() - minutes * 60)
    out["minutes"] = minutes
    out["resolver"] = dns.stats() if dns else {"running": False, "enabled": False}
    return out


@router.get("/api/v1/dns/devices")
async def dns_devices(request: Request, minutes: int = 60,
                      limit: int = Query(25, le=500)):
    return {"minutes": minutes, "devices": await request.app.state.store.dns_by_device(
        time.time() - minutes * 60, limit)}


@router.get("/api/v1/dns/domains")
async def dns_domains(request: Request, minutes: int = 60,
                      limit: int = Query(25, le=500), client_ip: str | None = None):
    return {"minutes": minutes, "domains": await request.app.state.store.dns_top_domains(
        time.time() - minutes * 60, limit, client_ip)}


@router.get("/api/v1/dns/recent")
async def dns_recent(request: Request, limit: int = Query(50, le=1000),
                     client_ip: str | None = None):
    return {"queries": await request.app.state.store.dns_recent(limit, client_ip)}


@router.get("/api/v1/dns/search")
async def dns_search(request: Request, pattern: str, minutes: int = 1440,
                     limit: int = Query(100, le=2000)):
    rows = await request.app.state.store.dns_search(
        pattern, time.time() - minutes * 60, limit)
    return {"pattern": pattern, "matches": len(rows), "rows": rows}


@router.get("/api/v1/lan/devices")
async def lan_devices(request: Request, limit: int = Query(100, le=1000)):
    return {"devices": await request.app.state.store.list_lan_devices(limit)}


# --------------------------------------------------------------------------
# MCP over HTTP
# --------------------------------------------------------------------------
@router.post("/api/v1/mcp/rpc")
async def mcp_rpc(request: Request) -> Any:
    try:
        body = await _json_body(request)
    except Exception:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    server = request.app.state.mcp
    admin = _is_admin(request)
    if isinstance(body, list):
        if not body:
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "empty batch"}}
        out = [r for r in [await server.handle(b, authorized=admin) for b in body] if r is not None]
        return out if out else Response(status_code=202)
    resp = await server.handle(body, authorized=admin)
    # A notification gets no JSON-RPC response: 202 Accepted, empty body.
    return resp if resp is not None else Response(status_code=202)


# --------------------------------------------------------------------------
# Flasher support: firmware images + provisioning defaults
# --------------------------------------------------------------------------
_FW_NAME = re.compile(r"^[a-z0-9_]+\.bin$")


@router.get("/api/v1/firmware/manifest")
async def firmware_manifest():
    path = Path(settings.firmware_dist) / "manifest.json"
    if not path.exists():
        raise HTTPException(status_code=404,
                            detail="no firmware built - run: python firmware/build.py")
    return json.loads(path.read_text())


@router.get("/api/v1/firmware/{name}")
async def firmware_file(name: str):
    if not _FW_NAME.match(name):
        raise HTTPException(status_code=400, detail="bad image name")
    path = Path(settings.firmware_dist) / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="image not built")
    return FileResponse(str(path), media_type="application/octet-stream",
                        headers={"Cache-Control": "no-cache"})


@router.get("/api/v1/provisioning/defaults")
async def provisioning_defaults(request: Request):
    """What the flasher pre-fills: where boards should report, and (only for an
    admin, or in open lab mode) the fleet enrolment token."""
    from app.ingest.discovery import choose_advertise_ip
    disc = getattr(request.app.state, "discovery", None)
    ds = disc.stats() if disc else {}
    # Never hand the flasher an address this host does not actually have.
    host, warning = (settings.public_host, None) if settings.public_host else \
        choose_advertise_ip(settings.mdns_advertise_ip)
    out = {
        "server": host,
        "mdns_host": f"{ds.get('hostname', 'ionity-fleet.local')}".rstrip("."),
        "mqtt_port": settings.mqtt_port,
        "http_port": settings.port,
        "site": "lab",
        "group": "bench",
        "fleet_name": settings.fleet_name,
        "require_token": settings.require_token,
        "admin": _is_admin(request),
        "warning": warning,
    }
    if _is_admin(request):
        out["fleet_token"] = settings.fleet_token
        out["mqtt_user"] = settings.mqtt_username
    return out


@router.get("/api/v1/integrations")
async def integrations(request: Request):
    return {k: v.stats() for k, v in getattr(request.app.state, "integrations", {}).items()}


@router.post("/api/v1/devices/{device_id}/mcp")
async def device_mcp(request: Request, device_id: str):
    """Relay one JSON-RPC request to a board's own MCP server over MQTT.
    Write tools need the admin token, same as /cmd."""
    try:
        rpc = await _json_body(request)
    except Exception:
        raise HTTPException(status_code=400, detail="body must be a JSON-RPC object") from None
    if not isinstance(rpc, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON-RPC object")
    from app.mcp.tools import DEVICE_READ_TOOLS
    if rpc.get("method") == "tools/call":
        tool = (rpc.get("params") or {}).get("name")
        if tool not in DEVICE_READ_TOOLS and not _is_admin(request):
            raise HTTPException(status_code=401, detail="admin token required for write tools")
    r = await request.app.state.registry.call_device(device_id, rpc, settings.device_rpc_timeout_s)
    if not r.get("ok") and "response" not in r:
        raise HTTPException(status_code=504 if "no reply" in r.get("error", "") else 409,
                            detail=r.get("error"))
    return r.get("response")


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
@router.get("/api/v1/health")
async def health(request: Request):
    app = request.app
    reg = app.state.registry
    bridge = getattr(app.state, "mqtt", None)
    return {
        "ok": True,
        "fleet": settings.fleet_name,
        "uptime_s": round(time.time() - reg.started_at, 1),
        "devices_known": len(reg.devices),
        "queue_depth": reg.queue.qsize(),
        "dropped_writes": reg.dropped,
        "storage": settings.storage_driver,
        "mqtt": bridge.stats() if bridge else {"connected": False, "enabled": False},
        "dns": (getattr(app.state, "dns", None).stats()
                if getattr(app.state, "dns", None) else {"running": False, "enabled": False}),
        "discovery": (getattr(app.state, "discovery", None).stats()
                      if getattr(app.state, "discovery", None) else {"enabled": False}),
        "ws_clients": len(ws_clients),
        "admin_token_required": bool(settings.admin_token),
        "mcp": {"protocol": mcp_protocol.LATEST_VERSION, "server": mcp_protocol.SERVER_VERSION,
                "tools": len(MCP_TOOLS)},
        "datadog": (app.state.datadog.stats() if getattr(app.state, "datadog", None)
                    else {"enabled": False}),
    }


# --------------------------------------------------------------------------
# WebSocket live feed
# --------------------------------------------------------------------------
@router.websocket("/ws/fleet")
async def ws_fleet(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    try:
        await ws.send_json(ws.app.state.registry.dashboard_payload())
        while True:
            await asyncio.sleep(30)
            await ws.send_json({"type": "keepalive"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ws_clients.discard(ws)
