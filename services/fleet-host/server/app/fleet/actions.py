"""
AEDI - IONITY GLOBAL | Node actions - one verb list for every node generation
Doc ID: DOC-2026-10-ESP32MCP-ACTIONS | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

The portal's quick actions, REST POST /api/v1/nodes/{id}/action and the MCP
tool trigger_node_action all land here, so an action means the same thing
everywhere:

  ping            round trip to the node (EWP: read_telemetry with no sections; fw 2.x: "ping")
  identify        blink the identify LED
  reboot          restart the node (reply first)
  set_alert_led   on/off (+ optional lease_s: the node switches it off itself)
  arp_scan        sweep the node's /24 so silent LAN devices show up
  flush_dns_cache empty the node's DNS answer cache
  sync_watchlist  push the host's watchlist now
  network_stats   forwarder counters + upstream health
  local_devices   the node's LAN client table
  dns_log         the node's most recent queries
  ping_target     ICMP echo FROM the node to a host (target, count?, timeout_ms?, interval_ms?) -
                  reachability as that board sees it (EWP tool ping_host, firmware 3.2 / Pi agent 2.1)

EWP/1 nodes (firmware 3, Pi agent 2) get REQ frames through the hub; fw 2.x
JSON boards get their legacy commands where an equivalent exists.
"""
from __future__ import annotations

import re
import time
from typing import Any

from app.ewp import ewp_protocol as P

ACTIONS = ("ping", "identify", "reboot", "set_alert_led", "arp_scan", "flush_dns_cache", "sync_watchlist",
           "network_stats", "local_devices", "dns_log", "ping_target")
DESTRUCTIVE = {"reboot", "set_alert_led"}

_TARGET_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$")
PING_ERRORS = {v: k.lower() for k, v in ((n[len("PING_"):], getattr(P, n)) for n in dir(P)
                                        if n.startswith("PING_") and n != "PING_NAMES")}


def ping_args(p: dict[str, Any]) -> dict[str, Any]:
    """Validate ping_target params against the EWP contract (EWP_LIMITS.ping*) -> PingHostArgs fields."""
    L = P.LIMITS
    target = str(p.get("target") or "").strip()
    if not _TARGET_RE.match(target):
        raise ActionError("ping_target needs target: a host name or IPv4 address")

    def bounded(name: str, default: int, lo: int, hi: int) -> int:
        v = p.get(name, default)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ActionError(f"ping_target {name} must be an integer {lo}-{hi}")
        return v
    return {"target": target,
            "count": bounded("count", 4, 1, L["pingCountMax"]),
            "timeoutMs": bounded("timeout_ms", 1000, L["pingTimeoutMinMs"], L["pingTimeoutMaxMs"]),
            "intervalMs": bounded("interval_ms", 1000, L["pingIntervalMinMs"], L["pingIntervalMaxMs"])}


class ActionError(ValueError):
    pass


async def node_action(device_id: str, action: str, params: dict[str, Any] | None, *, registry, hub) -> dict[str, Any]:
    params = params or {}
    if action not in ACTIONS:
        raise ActionError(f"unknown action '{action}' (one of: {', '.join(ACTIONS)})")
    d = registry.devices.get(device_id)
    if d is None:
        raise ActionError(f"unknown device '{device_id}'")
    if action == "ping_target":
        args = ping_args(params)              # validate before anything is sent
        if d.get("protocol") != "ewp1":
            raise ActionError(f"'ping_target' needs firmware 3.2+ or Pi agent 2.1+ - {device_id} runs the fw 2.x protocol")
        return await _ping_target(device_id, args, hub)
    if d.get("protocol") == "ewp1":
        return await _ewp(device_id, action, params, hub)
    return await _legacy(device_id, action, params, registry)


async def _ewp(hwid: str, action: str, p: dict[str, Any], hub) -> dict[str, Any]:
    if action == "ping":
        t0 = time.monotonic()
        r = await hub.call(hwid, P.TOOL_READ_TELEMETRY, {"sections": 0}, timeout_s=5)
        out = {"ok": r["ok"], "device_id": hwid, "action": action,
               "rtt_ms": round((time.monotonic() - t0) * 1000, 1)}
        if not r["ok"]:
            out["error"] = r.get("error")
        else:
            out["uptime_s"] = (r.get("result") or {}).get("uptimeS")
        return out
    if action == "identify":
        r = await hub.call(hwid, P.TOOL_IDENTIFY, {"durationMs": int(p.get("duration_ms", 1500))})
    elif action == "reboot":
        r = await hub.call(hwid, P.TOOL_REBOOT, {"delayMs": int(p.get("delay_ms", 500))})
    elif action == "set_alert_led":
        ch = hub.actuator_channel(hwid)
        if ch is None:
            raise ActionError(f"{hwid} has no actuator channel for an alert LED")
        on = p.get("on")
        if not isinstance(on, bool):
            raise ActionError("set_alert_led needs on: true|false")
        args: dict[str, Any] = {"channel": ch, "value": 1.0 if on else 0.0}
        if p.get("lease_s"):
            args["leaseMs"] = int(float(p["lease_s"]) * 1000)
        r = await hub.call(hwid, P.TOOL_SET_ACTUATOR, args)
    elif action == "arp_scan":
        r = await hub.call(hwid, P.TOOL_SCAN_LAN, {})
    elif action == "flush_dns_cache":
        r = await hub.call(hwid, P.TOOL_FLUSH_DNS_CACHE, {})
    elif action == "sync_watchlist":
        return {"action": action, **await hub.sync_watchlist(hwid, force=True)}
    elif action == "network_stats":
        r = await hub.call(hwid, P.TOOL_GET_NETWORK_STATS, {})
    elif action == "local_devices":
        r = await hub.call(hwid, P.TOOL_GET_LOCAL_DEVICES, {"limit": int(p.get("limit", P.LIMITS["localDevices"]))})
    else:  # dns_log
        r = await hub.call(hwid, P.TOOL_QUERY_DNS_LOG, {"limit": int(p.get("limit", 16)),
                                                        "flaggedOnly": bool(p.get("flagged_only", False))})
    return {"device_id": hwid, "action": action, **r}


async def _ping_target(hwid: str, args: dict[str, Any], hub) -> dict[str, Any]:
    # The node answers after count x interval + timeout; give it that plus the round trip.
    budget_s = ((args["count"] - 1) * args["intervalMs"] + args["timeoutMs"]) / 1000.0 + 2.0
    r = await hub.call(hwid, P.TOOL_PING_HOST, args, timeout_s=budget_s)
    out: dict[str, Any] = {"device_id": hwid, "action": "ping_target", "target": args["target"], **r}
    res = r.get("result") if r.get("ok") else None
    if isinstance(res, dict):
        err = res.get("error", 0)
        out["reachable"] = err == P.PING_NONE and (res.get("received") or 0) > 0
        out["result"] = {**res, "error": PING_ERRORS.get(err, err)}
        sent = res.get("sent") or 0
        out["loss_pct"] = round(100.0 * (sent - (res.get("received") or 0)) / sent, 1) if sent else None
    return out


async def _legacy(device_id: str, action: str, p: dict[str, Any], registry) -> dict[str, Any]:
    """fw 2.x / Pico JSON boards: the subset they implement."""
    if action in ("ping", "identify", "reboot"):
        r = await registry.send_command(device_id, action, {})
        return {"device_id": device_id, "action": action, **r,
                "note": "fw 2.x replies asynchronously - see get_command_results with the cmd_id"}
    if action == "set_alert_led":
        on = p.get("on")
        if not isinstance(on, bool):
            raise ActionError("set_alert_led needs on: true|false")
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "set_actuator", "arguments": {"channel": p.get("channel", "pwm0"),
                                                                 "value": 1 if on else 0}}}
        from app.config import settings
        r = await registry.call_device(device_id, rpc, settings.device_rpc_timeout_s)
        return {"device_id": device_id, "action": action, **r}
    raise ActionError(f"'{action}' needs a NetObserve node (firmware 3.1+ or Pi agent 2) - {device_id} runs "
                      f"the fw 2.x protocol")
