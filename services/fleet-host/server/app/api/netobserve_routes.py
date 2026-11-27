"""
AEDI - IONITY GLOBAL | REST + WebSocket for EWP/1 nodes, LAN DNS, watchlist and labels
Doc ID: DOC-2026-10-ESP32MCP-NETAPI | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Node ingest (fleet token, rate limited):
  POST   /api/v1/ewp/{hwid}/{channel}?site=   one raw EWP/1 frame (HTTP fallback of the MQTT topics)
  POST   /api/v1/nodes/register               pairing: token check -> broker, topics, watchlist version

Operators (reads open on the LAN, writes need the admin token):
  POST   /api/v1/nodes/{id}/action            {action, params}: ping, identify, reboot, set_alert_led,
                                              arp_scan, flush_dns_cache, sync_watchlist, network_stats,
                                              local_devices, dns_log, ping_target (rate limited)
  GET    /api/v1/dns/queries                  filtered, paginated DNS log
  GET    /api/v1/dns/flagged                  watchlist hits with client attribution + hit counts
  GET    /api/v1/watchlist                    rules + categories + version
  POST   /api/v1/watchlist/rules              {pattern, category, alert}
  PATCH  /api/v1/watchlist/rules/{rule}       {enabled}
  DELETE /api/v1/watchlist/rules/{rule}
  POST   /api/v1/watchlist/categories         {name, label}
  DELETE /api/v1/watchlist/categories/{name}
  POST   /api/v1/watchlist/sync               push to every NetObserve node now
  GET    /api/v1/labels                       config/device_labels.json + discovered names
  PUT    /api/v1/labels/{key}                 {label}
  DELETE /api/v1/labels/{key}
  WS     /ws/events                           live: dns queries, flags, node state
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.config import settings
from app.ewp import ewp_protocol as P
from app.fleet.actions import ACTIONS, DESTRUCTIVE, ActionError, node_action
from app.rules.engine import RuleError
from app.security import TokenBucket, check_fleet_token, ingest_limiter, rate_limit, require_admin

router = APIRouter()
MAX_FRAME = P.LIMITS["uplinkFrame"]


# --------------------------------------------------------------------------
# Node ingest
# --------------------------------------------------------------------------
@router.post("/api/v1/ewp/{hwid}/{channel}")
async def ewp_ingest(request: Request, hwid: str, channel: str, site: str = Query("lab", max_length=32)):
    rate_limit(request, ingest_limiter)
    body = await request.body()
    check_fleet_token(request, body)
    if len(body) > MAX_FRAME:
        raise HTTPException(status_code=413, detail=f"frame larger than {MAX_FRAME} bytes")
    if channel not in P.CHANNEL_POLICY or P.CHANNEL_POLICY[channel]["dir"] != "up":
        raise HTTPException(status_code=404, detail="unknown uplink channel")
    hub = request.app.state.ewp
    if not hub.handle_frame(site, hwid, channel, body, transport="http"):
        raise HTTPException(status_code=400, detail="frame rejected (bad hwid/site, bad frame or wrong channel)")
    return {"ok": True}


class RegisterIn(BaseModel):
    hwid: str = Field(pattern=r"^(?:esp32-[0-9a-f]{12}|pi-[0-9a-f]{8,16})$")
    site: str = Field(default="lab", pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")
    kind: str | None = None
    fw: str | None = None


@router.post("/api/v1/nodes/register")
async def node_register(request: Request, reg: RegisterIn):
    """Pairing handshake for EWP/1 nodes that start without a broker address
    (Pi agent on first boot, bench tools). The token proves membership; the
    answer says where to publish."""
    rate_limit(request, ingest_limiter)
    check_fleet_token(request, await request.body())
    from app.ingest.discovery import choose_advertise_ip
    host, _warn = (settings.public_host, None) if settings.public_host else choose_advertise_ip(settings.mdns_advertise_ip)
    return {
        "ok": True, "hwid": reg.hwid,
        "mqtt": {"host": host, "port": settings.mqtt_port, "username": settings.mqtt_username or None},
        "topics": {ch: f"{P.TOPIC_ROOT}/{reg.site}/{reg.hwid}/{ch}" for ch in P.CHANNEL_POLICY},
        "broadcast": P.BROADCAST_TOPIC,
        "http_ingest": f"http://{host}:{settings.port}/api/v1/ewp/{reg.hwid}/{{channel}}?site={reg.site}",
        "watchlist_version": request.app.state.rules.version,
        "server_time": time.time(),
    }


# --------------------------------------------------------------------------
# Node actions
# --------------------------------------------------------------------------
class ActionIn(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)


# ping_target makes a board send ICMP: at most one every 2 s per caller (burst 3).
ping_limiter = TokenBucket(0.5, 3)


@router.post("/api/v1/nodes/{device_id}/action")
async def node_action_route(request: Request, device_id: str, a: ActionIn):
    if a.action not in ACTIONS:
        raise HTTPException(status_code=422, detail=f"action must be one of {', '.join(ACTIONS)}")
    if a.action == "ping_target":
        rate_limit(request, ping_limiter)
    if a.action in DESTRUCTIVE or a.action in ("arp_scan", "flush_dns_cache", "sync_watchlist"):
        require_admin(request)
    try:
        return await node_action(device_id, a.action, a.params, registry=request.app.state.registry,
                                 hub=request.app.state.ewp)
    except ActionError as e:
        raise HTTPException(status_code=404 if "unknown device" in str(e) else 422, detail=str(e)) from None


# --------------------------------------------------------------------------
# DNS
# --------------------------------------------------------------------------
def _ts(v: str | None) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        from datetime import datetime
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            raise HTTPException(status_code=422, detail=f"'{v}' is not a unix time or ISO-8601 timestamp") from None


@router.get("/api/v1/dns/queries")
async def dns_queries(request: Request, domain: str | None = None, client_ip: str | None = None,
                      mac: str | None = None, label: str | None = None, category: str | None = None,
                      node_id: str | None = None, flagged_only: bool = False, since: str | None = None,
                      until: str | None = None, minutes: int | None = Query(None, ge=1, le=60 * 24 * 31),
                      limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    s = _ts(since) if since else (time.time() - minutes * 60 if minutes else None)
    return await request.app.state.store.query_dns(
        domain_pattern=domain, client_ip=client_ip, mac=mac, label=label, category=category, node_id=node_id,
        flagged_only=flagged_only, since=s, until=_ts(until), limit=limit, offset=offset)


@router.get("/api/v1/dns/flagged")
async def dns_flagged(request: Request, minutes: int = Query(60 * 24, ge=1, le=60 * 24 * 31),
                      category: str | None = None, client_ip: str | None = None, label: str | None = None,
                      active_only: bool = False, limit: int = Query(100, ge=1, le=1000),
                      offset: int = Query(0, ge=0)):
    rows = await request.app.state.store.list_flags(
        since=time.time() - minutes * 60, category=category, client_ip=client_ip, label=label,
        active_within_s=settings.watch_hold_s if active_only else None, limit=limit, offset=offset)
    now = time.time()
    for r in rows:
        r["active"] = now - r["last_ts"] <= settings.watch_hold_s
    return {"minutes": minutes, "flags": rows, "total_hits": sum(r["hits"] for r in rows),
            "watch": request.app.state.netwatch.stats() if getattr(request.app.state, "netwatch", None) else None}


# --------------------------------------------------------------------------
# Watchlist
# --------------------------------------------------------------------------
class RuleIn(BaseModel):
    pattern: str = Field(min_length=3, max_length=100)
    category: str = Field(min_length=2, max_length=48)
    alert: bool = True


class RulePatch(BaseModel):
    enabled: bool


class CategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=48)
    label: str | None = Field(default=None, max_length=48)


def _rule_error(e: RuleError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(e))


@router.get("/api/v1/watchlist")
async def watchlist(request: Request):
    snap = request.app.state.rules.snapshot()
    nodes = []
    for did, d in request.app.state.registry.devices.items():
        if "set_watchlist" in (d.get("tools") or []):
            v = (d.get("net") or {}).get("watchVer") if isinstance(d.get("net"), dict) else None
            nodes.append({"device_id": did, "label": d.get("label"), "version": v,
                          "in_sync": v == snap["version"]})
    return {**snap, "nodes": nodes}


@router.post("/api/v1/watchlist/rules", status_code=201)
async def add_rule(request: Request, r: RuleIn):
    require_admin(request)
    try:
        rule = await request.app.state.rules.add_rule(r.pattern, r.category, r.alert)
    except RuleError as e:
        raise _rule_error(e) from None
    return request.app.state.rules.rule_view(rule)


@router.patch("/api/v1/watchlist/rules/{rule}")
async def patch_rule(request: Request, rule: str, p: RulePatch):
    require_admin(request)
    try:
        r = await request.app.state.rules.set_enabled(rule, p.enabled)
    except RuleError as e:
        raise _rule_error(e) from None
    return request.app.state.rules.rule_view(r)


@router.delete("/api/v1/watchlist/rules/{rule:path}")
async def delete_rule(request: Request, rule: str):
    require_admin(request)
    try:
        return await request.app.state.rules.remove_rule(rule)
    except RuleError as e:
        raise _rule_error(e) from None


@router.post("/api/v1/watchlist/categories", status_code=201)
async def add_category(request: Request, c: CategoryIn):
    require_admin(request)
    try:
        cat = await request.app.state.rules.add_category(c.name, c.label)
    except RuleError as e:
        raise _rule_error(e) from None
    return {"id": cat.id, "name": cat.name, "label": cat.label}


@router.delete("/api/v1/watchlist/categories/{name}")
async def delete_category(request: Request, name: str):
    require_admin(request)
    try:
        return await request.app.state.rules.remove_category(name)
    except RuleError as e:
        raise _rule_error(e) from None


@router.post("/api/v1/watchlist/sync")
async def sync_watchlist(request: Request):
    require_admin(request)
    return {"version": request.app.state.rules.version, "nodes": await request.app.state.ewp.sync_all()}


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------
class LabelIn(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    site: str | None = None
    group: str | None = None


@router.get("/api/v1/labels")
async def labels(request: Request, discovered: int = Query(200, ge=0, le=1000)):
    book = request.app.state.labels
    lan = await request.app.state.store.list_lan_devices(discovered) if discovered else []
    return {"file": str(book.path), "error": book.error, "entries": book.entries(),
            "discovered": [{"ip": d["ip"], "mac": d.get("mac"), "hostname": d.get("hostname"),
                            "vendor": d.get("vendor"), "label": d.get("label"), "last_seen": d.get("last_seen"),
                            "node_id": d.get("node_id")} for d in lan]}


@router.put("/api/v1/labels/{key}")
async def put_label(request: Request, key: str, body: LabelIn):
    require_admin(request)
    try:
        entry = await asyncio.to_thread(request.app.state.labels.set, key, body.label, site=body.site,
                                        group=body.group)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return entry


@router.delete("/api/v1/labels/{key}")
async def delete_label(request: Request, key: str):
    require_admin(request)
    if not await asyncio.to_thread(request.app.state.labels.remove, key):
        raise HTTPException(status_code=404, detail="no such label")
    return {"removed": key}


# --------------------------------------------------------------------------
# Live events
# --------------------------------------------------------------------------
@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """Batched every 300 ms: {"type":"batch","dns":[...],"flags":[...],"nodes":[...]}.
    The first message is a snapshot (recent queries + recent flags) so a fresh
    page is never empty."""
    await ws.accept()
    app = ws.app
    q = app.state.pipeline.subscribe()
    try:
        recent = await app.state.store.dns_recent(100)
        flags = await app.state.store.list_flags(since=time.time() - 86400, limit=50)
        await ws.send_text(json.dumps({"type": "snapshot", "dns": recent, "flags": flags,
                                       "watchlist_version": app.state.rules.version}, default=str))
        while True:
            try:
                first = await asyncio.wait_for(q.get(), 25)
            except asyncio.TimeoutError:
                await ws.send_text('{"type":"keepalive"}')
                continue
            msgs = [first]
            await asyncio.sleep(0.3)
            while not q.empty() and len(msgs) < 1000:
                msgs.append(q.get_nowait())
            out: dict[str, list] = {"dns": [], "flags": [], "nodes": []}
            for m in msgs:
                if m["type"] == "dns":
                    out["dns"].append(m["query"])
                elif m["type"] == "flag":
                    out["flags"].append(m["flag"])
                elif m["type"] == "node":
                    out["nodes"].append({k: v for k, v in m.items() if k != "type"})
            await ws.send_text(json.dumps({"type": "batch", **out}, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        app.state.pipeline.unsubscribe(q)


