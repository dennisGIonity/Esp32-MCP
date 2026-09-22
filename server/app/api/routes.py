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
/api/v1/health             GET   liveness + ingest stats
/ws/fleet                  WS    live dashboard feed
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request, HTTPException, Header, WebSocket, WebSocketDisconnect, Query

from app.config import settings
from app.models import TelemetryIn, CommandIn

router = APIRouter()
ws_clients: set[WebSocket] = set()


def _check_token(token: str | None) -> None:
    if settings.require_token and token != settings.fleet_token:
        raise HTTPException(status_code=401, detail="invalid or missing X-Fleet-Token")


# --------------------------------------------------------------------------
# Ingest (HTTP fallback path - MQTT is primary)
# --------------------------------------------------------------------------
@router.post("/api/v1/telemetry")
async def ingest(request: Request, x_fleet_token: str | None = Header(default=None)):
    _check_token(x_fleet_token)
    body = await request.json()
    registry = request.app.state.registry

    items = body if isinstance(body, list) else [body]
    if len(items) > 500:
        raise HTTPException(status_code=413, detail="max 500 readings per batch")

    accepted = 0
    for raw in items:
        try:
            t = TelemetryIn(**raw)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))
        t.net.transport = "http"
        registry.ingest(t)
        accepted += 1

    return {"ok": True, "accepted": accepted, "queue_depth": registry.queue.qsize()}


@router.post("/api/v1/devices/register")
async def register(request: Request, x_fleet_token: str | None = Header(default=None)):
    """Optional first-boot handshake. Returns the effective config a node
    should adopt, so a batch can be re-tagged centrally."""
    _check_token(x_fleet_token)
    body = await request.json()
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
    _check_token(x_fleet_token)
    payload = {k: v for k, v in cmd.model_dump().items()
               if k != "action" and v is not None}
    return await request.app.state.registry.send_command(device_id, cmd.action, payload)


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
    body = await request.json()
    server = request.app.state.mcp
    if isinstance(body, list):
        out = [r for r in [await server.handle(b) for b in body] if r is not None]
        return out
    resp = await server.handle(body)
    return resp if resp is not None else {"jsonrpc": "2.0", "result": {}}


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
