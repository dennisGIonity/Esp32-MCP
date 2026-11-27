"""
AEDI - IONITY GLOBAL | MCP resources + resource templates + prompts
Doc ID: DOC-2026-10-ESP32MCP-MCPRES | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Resources (JSON, subscribable - the server sends notifications/resources/updated):
  esp32://devices                         live fleet summary + every node
  esp32://dns/recent                      rolling window of recent LAN DNS queries
  esp32://dns/flagged                     active + recent watchlist hits
  esp32://config/watchlist                the domain flagging rules
  esp32://config/labels                   config/device_labels.json
Templates:
  esp32://devices/{device_id}             one node
  esp32://devices/{device_id}/telemetry   one node's telemetry snapshot (+ last hour of key metrics)
  esp32://dns/clients/{client_ip}         one LAN client's DNS timeline
The v2 URIs ionity://fleet/{summary,devices,schema} still read.
"""
from __future__ import annotations

import re
import time
from typing import Any

from app.config import settings

RESOURCES: list[dict[str, Any]] = [
    {"uri": "esp32://devices", "name": "devices", "title": "Fleet: every node",
     "description": "Fleet summary and every ESP32 / Pi node with live status, IP, MAC, RSSI, heap, last seen.",
     "mimeType": "application/json"},
    {"uri": "esp32://dns/recent", "name": "dns-recent", "title": "Recent LAN DNS queries",
     "description": "The last 15 minutes of LAN DNS lookups (max 300 rows), newest first, with client attribution.",
     "mimeType": "application/json"},
    {"uri": "esp32://dns/flagged", "name": "dns-flagged", "title": "Flagged activity",
     "description": "Watchlist hits of the last 24 h (active ones first): who, which category, how many hits.",
     "mimeType": "application/json"},
    {"uri": "esp32://config/watchlist", "name": "watchlist", "title": "DNS watchlist",
     "description": "Domain flagging rules and categories, with the version pushed to the nodes.",
     "mimeType": "application/json"},
    {"uri": "esp32://config/labels", "name": "labels", "title": "Device labels",
     "description": "Friendly names by MAC / IP / node id (config/device_labels.json).",
     "mimeType": "application/json"},
]

TEMPLATES: list[dict[str, Any]] = [
    {"uriTemplate": "esp32://devices/{device_id}", "name": "device", "title": "One node",
     "description": "Current state of one node.", "mimeType": "application/json"},
    {"uriTemplate": "esp32://devices/{device_id}/telemetry", "name": "device-telemetry",
     "title": "Node telemetry snapshot",
     "description": "Latest metrics of one node plus the last hour of heap, RSSI, CPU and DNS counters.",
     "mimeType": "application/json"},
    {"uriTemplate": "esp32://dns/clients/{client_ip}", "name": "client-dns", "title": "One client's DNS timeline",
     "description": "The last 24 h of lookups by one LAN client (max 500), with its flagged activity.",
     "mimeType": "application/json"},
]

_DEV = re.compile(r"^esp32://devices/([a-z0-9-]{3,64})$")
_DEV_TM = re.compile(r"^esp32://devices/([a-z0-9-]{3,64})/telemetry$")
_CLIENT = re.compile(r"^esp32://dns/clients/([0-9a-fA-F:.]{2,45})$")


class ResourceNotFound(KeyError):
    pass


async def read_resource(uri: str, ctx) -> Any:
    from app.mcp.tools import DeviceTelemetryArgs, _device_telemetry, _node_row, iso
    reg, store = ctx.registry, ctx.store
    if uri in ("esp32://devices", "ionity://fleet/summary", "ionity://fleet/devices"):
        views = reg.list_views(limit=2000)
        if uri == "ionity://fleet/summary":
            return reg.summary().model_dump()
        if uri == "ionity://fleet/devices":
            return [v.model_dump() for v in views]
        return {"summary": reg.summary().model_dump(), "devices": [_node_row(v) for v in views]}
    if uri == "ionity://fleet/schema":
        seen: dict[str, Any] = {}
        for d in reg.devices.values():
            for k, v in (d.get("metrics") or {}).items():
                seen.setdefault(k, {"sample": v, "type": type(v).__name__})
        return {"metrics": seen, "device_count": len(reg.devices)}
    if uri == "esp32://dns/recent":
        out = await store.query_dns(since=time.time() - 900, limit=300)
        for r in out["queries"]:
            r["time"] = iso(r["ts"])
        return {"window_s": 900, **out}
    if uri == "esp32://dns/flagged":
        rows = await store.list_flags(since=time.time() - 86400, limit=200)
        now = time.time()
        for r in rows:
            r["active"] = now - r["last_ts"] <= settings.watch_hold_s
            r["first_seen"], r["last_seen"] = iso(r["first_ts"]), iso(r["last_ts"])
        rows.sort(key=lambda r: (not r["active"], -r["last_ts"]))
        return {"active": sum(1 for r in rows if r["active"]), "hold_s": settings.watch_hold_s, "flags": rows}
    if uri == "esp32://config/watchlist":
        if ctx.rules is None:
            raise ResourceNotFound(uri)
        return ctx.rules.snapshot()
    if uri == "esp32://config/labels":
        if ctx.labels is None:
            raise ResourceNotFound(uri)
        return {"file": str(ctx.labels.path), "error": ctx.labels.error, "entries": ctx.labels.entries()}
    m = _DEV_TM.match(uri)
    if m:
        if m.group(1) not in reg.devices:
            raise ResourceNotFound(uri)
        return await _device_telemetry(DeviceTelemetryArgs(device_id=m.group(1), minutes=60, max_points=60), ctx)
    m = _DEV.match(uri)
    if m:
        v = reg.device_view(m.group(1))
        if v is None:
            raise ResourceNotFound(uri)
        return {**_node_row(v), "metrics": v.metrics, "tools": v.tools, "dns": v.dns}
    m = _CLIENT.match(uri)
    if m:
        ip = m.group(1)
        out = await store.query_dns(client_ip=ip, since=time.time() - 86400, limit=500)
        for r in out["queries"]:
            r["time"] = iso(r["ts"])
        dev = await store.lan_device(ip=ip)
        flags = await store.list_flags(since=time.time() - 86400, client_ip=ip, limit=100)
        return {"client": dev, "flags": flags, **out}
    raise ResourceNotFound(uri)


async def complete(ref: dict, argument: dict, ctx) -> list[str]:
    """completion/complete for template variables and prompt arguments."""
    name, value = argument.get("name"), str(argument.get("value") or "")
    if name == "device_id":
        vals = sorted(ctx.registry.devices)
    elif name == "client_ip":
        vals = [d["ip"] for d in await ctx.store.list_lan_devices(500)]
    elif name == "device_label":
        vals = sorted({d["label"] for d in await ctx.store.list_lan_devices(500) if d.get("label")})
    elif name == "category" and ctx.rules is not None:
        vals = sorted(c.name for c in ctx.rules.categories.values())
    else:
        vals = []
    return [v for v in vals if v.lower().startswith(value.lower())][:100]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
PROMPTS: list[dict[str, Any]] = [
    {"name": "network-activity-report", "title": "Network activity report",
     "description": "Gather DNS statistics, top domains, busiest devices and flagged alerts for a security / "
                    "activity summary of the LAN.",
     "arguments": [{"name": "minutes", "description": "look-back window in minutes (default 60)", "required": False},
                   {"name": "category", "description": "focus on one watchlist category, e.g. youtube",
                    "required": False}]},
    {"name": "investigate-client", "title": "Investigate one client",
     "description": "Analyse one LAN device's DNS timeline: what it looked up, when, flagged activity.",
     "arguments": [{"name": "device_label", "description": "friendly name (from device_labels.json)", "required": False},
                   {"name": "client_ip", "description": "IPv4 address (if no label)", "required": False},
                   {"name": "minutes", "description": "look-back window in minutes (default 240)", "required": False}]},
    {"name": "fleet_health_check", "title": "Fleet health check",
     "description": "Summarise fleet health, list anything stale/offline/alerting and suggest next steps.",
     "arguments": [{"name": "site", "description": "limit to one site", "required": False}]},
    {"name": "investigate_device", "title": "Investigate one node",
     "description": "Pull one node's state, telemetry and alerts and explain what is wrong.",
     "arguments": [{"name": "device_id", "description": "node to investigate", "required": True}]},
]


class PromptError(ValueError):
    pass


def _int(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = args.get(key)
    if v in (None, ""):
        return default
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise PromptError(f"{key} must be a whole number of minutes") from None
    if not lo <= n <= hi:
        raise PromptError(f"{key} must be {lo}-{hi}")
    return n


async def prompt_messages(name: str, args: dict, ctx) -> tuple[str, list[dict]]:
    args = {k: v for k, v in (args or {}).items() if v not in (None, "")}
    if name == "network-activity-report":
        minutes = _int(args, "minutes", 60, 1, 44640)
        focus = f" Focus on the '{args['category']}' category." if args.get("category") else ""
        flagged = await read_resource("esp32://dns/flagged", ctx)
        text = (f"Write a network activity report for the last {minutes} minutes.{focus}\n"
                f"1. Call dns_summary (minutes={minutes}) and dns_top_domains (minutes={minutes}, limit=15).\n"
                f"2. Call dns_by_device (minutes={minutes}) to see the busiest clients.\n"
                f"3. Call get_flagged_alerts (minutes={minutes}, group_by=client"
                f"{', category=' + repr(args['category']) if args.get('category') else ''}).\n"
                "Report: total lookups and clients, the top domains, every device with flagged activity "
                "(label or IP, category, hits, first/last time, still active?), anything unusual (a device "
                "suddenly very chatty, upstream failures, unknown devices). Be factual; say when data is "
                "missing (e.g. devices using DNS-over-HTTPS are not visible). Do not send any commands.")
        return (PROMPTS[0]["description"],
                [{"role": "user", "content": {"type": "text", "text": text}},
                 {"role": "user", "content": {"type": "resource", "resource": {
                     "uri": "esp32://dns/flagged", "mimeType": "application/json",
                     "text": _dump(flagged)}}}])
    if name == "investigate-client":
        minutes = _int(args, "minutes", 240, 1, 44640)
        label, ip = args.get("device_label"), args.get("client_ip")
        if not label and not ip:
            raise PromptError("investigate-client needs device_label or client_ip")
        who = f"label '{label}'" if label else f"IP {ip}"
        sel = f"device_label={label!r}" if label else f"client_ip={ip!r}"
        text = (f"Investigate the LAN client with {who} over the last {minutes} minutes.\n"
                f"1. query_dns_activity ({sel}, minutes={minutes}, limit=200) - page with offset if has_more.\n"
                f"2. get_flagged_alerts ({sel}, minutes={minutes}, group_by=event).\n"
                f"3. list_lan_devices to confirm its MAC, host name and vendor.\n"
                "Build a timeline: when it was active, which services it used (group domains into services), "
                "when watchlist categories were hit and for how long. Note gaps and limits honestly. "
                "Do not run any node actions.")
        msgs: list[dict] = [{"role": "user", "content": {"type": "text", "text": text}}]
        if ip:
            msgs.append({"role": "user", "content": {"type": "resource", "resource": {
                "uri": f"esp32://dns/clients/{ip}", "mimeType": "application/json",
                "text": _dump(await read_resource(f"esp32://dns/clients/{ip}", ctx))}}})
        return PROMPTS[1]["description"], msgs
    if name == "fleet_health_check":
        scope = f" for site '{args['site']}'" if args.get("site") else ""
        text = (f"Run fleet_summary{scope}, then list_devices with health=offline, stale and alerting, and "
                "get_alerts. Report: counts, each problem device with its last seen age and key metrics, likely "
                "causes, and concrete next steps. Do not send any commands without asking me first.")
        return PROMPTS[2]["description"], [{"role": "user", "content": {"type": "text", "text": text}}]
    if name == "investigate_device":
        did = args.get("device_id")
        if not did:
            raise PromptError("investigate_device needs device_id")
        text = (f"Investigate node {did}: call get_device_telemetry (minutes=240) and get_alerts for it, look at "
                "trends in RSSI, heap (free, largest block, fragmentation), CPU and DNS counters, and explain in "
                "plain words what is happening and what to do. Ask before running any node action.")
        return PROMPTS[3]["description"], [{"role": "user", "content": {"type": "text", "text": text}}]
    raise PromptError(f"unknown prompt '{name}'")


def _dump(v: Any) -> str:
    import json
    return json.dumps(v, indent=1, default=str)[:60_000]
