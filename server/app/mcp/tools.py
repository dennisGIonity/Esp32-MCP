"""
MCP tool surface for the Ionity ESP32 fleet.

One MCP server fronts the whole fleet. An AI agent (Claude, AEDi, Gemini)
sees seven tools and can reason about 1000 devices without knowing any of
their addresses.
"""
from __future__ import annotations

import time
from typing import Any

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "fleet_summary",
        "description": (
            "Health of the entire ESP32 fleet in one call: total/online/stale/"
            "offline/alerting counts, breakdown by site, group and firmware "
            "version, ingest rate and open alert count. Start here."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_devices",
        "description": (
            "List devices with their latest metrics and health. Filter by site, "
            "group, health state, or a free-text search over device_id/label/ip. "
            "Use limit/offset to page through a large fleet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "site": {"type": "string"},
                "group": {"type": "string"},
                "health": {"type": "string", "enum": ["online", "stale", "offline", "alerting"]},
                "search": {"type": "string"},
                "limit": {"type": "integer", "default": 50, "maximum": 2000},
                "offset": {"type": "integer", "default": 0},
            },
        },
    },
    {
        "name": "get_device",
        "description": "Full current state of one device, plus its recent telemetry history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "history_points": {"type": "integer", "default": 50, "maximum": 2000},
            },
            "required": ["device_id"],
        },
    },
    {
        "name": "query_telemetry",
        "description": (
            "Time-series query across the fleet. Give a metric name (e.g. temp_c, "
            "rssi_dbm, packet_loss_pct, free_heap_bytes) and a lookback window in "
            "minutes. Optionally scope to one device, site or group."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string"},
                "minutes": {"type": "integer", "default": 60},
                "device_id": {"type": "string"},
                "site": {"type": "string"},
                "group": {"type": "string"},
                "limit": {"type": "integer", "default": 500, "maximum": 20000},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "aggregate_metric",
        "description": (
            "Fleet-wide statistics for one metric over a window: count, mean, min, "
            "max, distinct reporting devices, and the ten devices with the highest "
            "average. Use this instead of query_telemetry when you want the shape "
            "of the fleet, not raw rows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "metric": {"type": "string"},
                "minutes": {"type": "integer", "default": 60},
                "site": {"type": "string"},
                "group": {"type": "string"},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "get_alerts",
        "description": "Open (or historical) alerts across the fleet, newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "open_only": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 50, "maximum": 500},
            },
        },
    },
    {
        "name": "send_command",
        "description": (
            "Send a command to one device, or to every device with "
            "device_id='broadcast'. Actions: reboot, identify (blink LED), ping, "
            "set_meta (re-tag site/group/label without reflashing). Requires MQTT."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "action": {"type": "string", "enum": ["reboot", "identify", "ping", "set_meta"]},
                "site": {"type": "string"},
                "group": {"type": "string"},
                "label": {"type": "string"},
            },
            "required": ["device_id", "action"],
        },
    },
]

MCP_TOOLS += [
    {
        "name": "dns_summary",
        "description": (
            "LAN DNS overview for a window: total queries, distinct domains, "
            "how many devices are asking, cache hit count and average latency. "
            "Start here for any 'what is my network doing' question."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"minutes": {"type": "integer", "default": 60}},
        },
    },
    {
        "name": "dns_by_device",
        "description": (
            "THE 'what is going to what device' tool. Per device on the LAN: "
            "IP, MAC, vendor, hostname, how many DNS queries it made, how many "
            "distinct domains, and its top 5 domains. Use this to see which "
            "machine is talking to what."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "default": 60},
                "limit": {"type": "integer", "default": 25, "maximum": 500},
            },
        },
    },
    {
        "name": "dns_top_domains",
        "description": (
            "Most-requested domains across the LAN, with hit count and how many "
            "distinct devices asked. Pass client_ip to scope it to one device."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "default": 60},
                "limit": {"type": "integer", "default": 25, "maximum": 500},
                "client_ip": {"type": "string"},
            },
        },
    },
    {
        "name": "dns_search",
        "description": (
            "Find every DNS query matching a substring, and which device made "
            "it. Use for questions like 'has anything on my network resolved "
            "tiktok' or 'who is talking to this domain'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "minutes": {"type": "integer", "default": 1440},
                "limit": {"type": "integer", "default": 100, "maximum": 2000},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "dns_recent",
        "description": "Live tail of the most recent DNS queries with the asking device.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50, "maximum": 1000},
                "client_ip": {"type": "string"},
            },
        },
    },
    {
        "name": "list_lan_devices",
        "description": (
            "Inventory of every device seen on the LAN: IP, MAC, vendor guessed "
            "from the OUI, reverse-DNS hostname, first/last seen and query count. "
            "This is the 'who is on my network' map."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 100, "maximum": 1000}},
        },
    },
]

MCP_RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "ionity://fleet/summary",
        "name": "Fleet summary",
        "description": "Live rollup of the ESP32 fleet.",
        "mimeType": "application/json",
    },
    {
        "uri": "ionity://fleet/devices",
        "name": "Device inventory",
        "description": "Every known device with its latest metrics.",
        "mimeType": "application/json",
    },
    {
        "uri": "ionity://fleet/schema",
        "name": "Telemetry schema",
        "description": "Metric names currently being reported, with sample values.",
        "mimeType": "application/json",
    },
]


async def execute(name: str, args: dict, registry, store, dns=None) -> Any:
    """Dispatch an MCP tool call against the live fleet."""
    # ---- LAN DNS visibility ---------------------------------------------
    if name.startswith("dns_") or name == "list_lan_devices":
        mins = int(args.get("minutes", 60))
        since = time.time() - mins * 60

        if name == "dns_summary":
            out = await store.dns_summary(since)
            out["minutes"] = mins
            out["resolver"] = dns.stats() if dns else {"running": False}
            return out
        if name == "dns_by_device":
            return {"minutes": mins,
                    "devices": await store.dns_by_device(since, int(args.get("limit", 25)))}
        if name == "dns_top_domains":
            return {"minutes": mins,
                    "domains": await store.dns_top_domains(
                        since, int(args.get("limit", 25)), args.get("client_ip"))}
        if name == "dns_search":
            since = time.time() - int(args.get("minutes", 1440)) * 60
            rows = await store.dns_search(args["pattern"], since,
                                          int(args.get("limit", 100)))
            return {"pattern": args["pattern"], "matches": len(rows), "rows": rows}
        if name == "dns_recent":
            return {"queries": await store.dns_recent(
                int(args.get("limit", 50)), args.get("client_ip"))}
        if name == "list_lan_devices":
            return {"devices": await store.list_lan_devices(int(args.get("limit", 100)))}

    if name == "fleet_summary":
        return registry.summary().model_dump()

    if name == "list_devices":
        views = registry.list_views(
            site=args.get("site"), group=args.get("group"),
            health=args.get("health"), search=args.get("search"),
            limit=int(args.get("limit", 50)), offset=int(args.get("offset", 0)),
        )
        return {"count": len(views), "devices": [v.model_dump() for v in views]}

    if name == "get_device":
        did = args["device_id"]
        view = registry.device_view(did)
        if not view:
            return {"error": f"unknown device '{did}'"}
        history = await store.query_telemetry(
            device_id=did, limit=int(args.get("history_points", 50))
        )
        return {"device": view.model_dump(), "history": history}

    if name == "query_telemetry":
        since = time.time() - int(args.get("minutes", 60)) * 60
        rows = await store.query_telemetry(
            device_id=args.get("device_id"), site=args.get("site"),
            group=args.get("group"), metric=args["metric"],
            since_s=since, limit=int(args.get("limit", 500)),
        )
        return {"metric": args["metric"], "points": len(rows), "rows": rows}

    if name == "aggregate_metric":
        since = time.time() - int(args.get("minutes", 60)) * 60
        return await store.aggregate_metric(
            args["metric"], since, site=args.get("site"), group=args.get("group")
        )

    if name == "get_alerts":
        return {"alerts": await store.list_alerts(
            device_id=args.get("device_id"),
            open_only=bool(args.get("open_only", True)),
            limit=int(args.get("limit", 50)),
        )}

    if name == "send_command":
        payload = {k: v for k, v in args.items()
                   if k in ("site", "group", "label") and v is not None}
        return await registry.send_command(args["device_id"], args["action"], payload)

    raise ValueError(f"Unknown tool '{name}'")


async def read_resource(uri: str, registry, store) -> Any:
    if uri == "ionity://fleet/summary":
        return registry.summary().model_dump()
    if uri == "ionity://fleet/devices":
        return [v.model_dump() for v in registry.list_views(limit=2000)]
    if uri == "ionity://fleet/schema":
        seen: dict[str, Any] = {}
        for d in registry.devices.values():
            for k, v in (d.get("metrics") or {}).items():
                seen.setdefault(k, {"sample": v, "type": type(v).__name__})
        return {"metrics": seen, "device_count": len(registry.devices)}
    raise ValueError(f"Unknown resource '{uri}'")
