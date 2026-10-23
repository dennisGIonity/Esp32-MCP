"""
AEDI - IONITY GLOBAL | MCP tools of the Ionity fleet host
Doc ID: DOC-2026-10-ESP32MCP-MCPTOOLS | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

One MCP server fronts every node - ESP32 firmware 2.x/3.x, Raspberry Pi agents,
Pico serial boards - and the LAN DNS the nodes observe. Each tool:

  * declares its input as a pydantic model: the JSON Schema clients see IS the
    validator (extra fields refused, types strict, ranges enforced), so what
    the schema promises is exactly what is accepted;
  * reports bad arguments and device failures as a tool result with
    isError: true and a sentence the model can act on (MCP 2025-11-25);
  * carries annotations (readOnly / destructive / idempotent / openWorld);
  * needs the admin token when it changes anything, and an explicit
    confirm: true for actions that touch hardware or delete data - the model
    is told to ask the user first (Ionity decision D10).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import settings
from app.ewp import ewp_protocol as P

# fw 2.x on-device MCP tool names (JSON boards) - kept for the REST relay and device_call_tool
DEVICE_READ_TOOLS = {"get_device_info", "read_telemetry", "run_inference",
                     "get_watchlist", "get_network_stats", "get_local_devices", "query_dns_log"}
DEVICE_WRITE_TOOLS = {"set_actuator", "set_state_mode", "identify", "set_watchlist", "scan_lan",
                      "flush_dns_cache", "reboot"}


class ToolArgError(ValueError):
    """Bad arguments - reported to the model as a tool error it can fix."""


class ToolFailure(RuntimeError):
    """The call was valid but could not be carried out (unknown device, node offline ...)."""


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _when(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
        except ValueError:
            pass
    raise ValueError("use unix seconds or ISO-8601, e.g. 2026-10-23T14:30:00+02:00")


def iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


Timestamp = float | str


# ---------------------------------------------------------------------------
# Argument models
# ---------------------------------------------------------------------------
class NoArgs(Args):
    pass


class ListDevicesArgs(Args):
    site: str | None = Field(None, max_length=32, description="only this site")
    group: str | None = Field(None, max_length=32)
    health: Literal["online", "stale", "offline", "alerting"] | None = None
    kind: Literal["esp32", "pi", "pico", "other"] | None = Field(None, description="node type")
    search: str | None = Field(None, max_length=64, description="substring of device_id, label or IP")
    limit: int = Field(200, ge=1, le=2000)
    offset: int = Field(0, ge=0)


class DeviceArgs(Args):
    device_id: str = Field(min_length=3, max_length=64, description="e.g. esp32-98a316e5d18c or pi-a1b2c3d4")


class GetDeviceArgs(DeviceArgs):
    history_points: int = Field(20, ge=0, le=500)


class DeviceTelemetryArgs(DeviceArgs):
    minutes: int = Field(60, ge=1, le=10080, description="look-back window (max 7 days)")
    metrics: list[str] | None = Field(None, max_length=20, description="metric names; default: the key "
                                      "hardware + network metrics this node reports")
    max_points: int = Field(300, ge=1, le=2000, description="per metric, newest first")


class QueryTelemetryArgs(Args):
    metric: str = Field(min_length=1, max_length=64)
    minutes: int = Field(60, ge=1, le=43200)
    device_id: str | None = Field(None, max_length=64)
    site: str | None = Field(None, max_length=32)
    group: str | None = Field(None, max_length=32)
    limit: int = Field(500, ge=1, le=20000)


class AggregateArgs(Args):
    metric: str = Field(min_length=1, max_length=64)
    minutes: int = Field(60, ge=1, le=43200)
    site: str | None = None
    group: str | None = None


class AlertsArgs(Args):
    device_id: str | None = Field(None, max_length=64)
    open_only: bool = True
    limit: int = Field(50, ge=1, le=500)


class DnsActivityArgs(Args):
    domain_pattern: str | None = Field(None, max_length=253, description=(
        "'youtube.com' = the domain and its subdomains; '*.googlevideo.com' = subdomains only; "
        "'tube' = substring"))
    client_ip: str | None = Field(None, max_length=45)
    client_mac: str | None = Field(None, max_length=17, description="aa:bb:cc:dd:ee:ff or aa-bb-...")
    device_label: str | None = Field(None, max_length=64, description="friendly name (substring match)")
    category: str | None = Field(None, max_length=48, description="e.g. youtube or video_streaming:youtube")
    node_id: str | None = Field(None, max_length=64, description="only queries seen by this ESP32/Pi node")
    flagged_only: bool = False
    since: Timestamp | None = Field(None, description="unix seconds or ISO-8601")
    until: Timestamp | None = None
    minutes: int | None = Field(None, ge=1, le=44640, description="shorthand for since = now - minutes")
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0, le=1_000_000)

    @field_validator("since", "until")
    @classmethod
    def _ts(cls, v):
        return _when(v)


class FlaggedArgs(Args):
    minutes: int = Field(60, ge=1, le=44640, description="look-back window")
    category: str | None = Field(None, max_length=48, description="e.g. youtube, tiktok, social_media")
    client_ip: str | None = Field(None, max_length=45)
    device_label: str | None = Field(None, max_length=64)
    active_only: bool = Field(False, description="only clients still active (within the hold time)")
    group_by: Literal["event", "client"] = Field("client", description="client = one row per device with "
                                                 "total hits; event = every flag episode")
    limit: int = Field(50, ge=1, le=500)


class DnsWindowArgs(Args):
    minutes: int = Field(60, ge=1, le=44640)
    limit: int = Field(25, ge=1, le=500)


class TopDomainsArgs(DnsWindowArgs):
    client_ip: str | None = Field(None, max_length=45)


class LanDevicesArgs(Args):
    limit: int = Field(100, ge=1, le=1000)


class WatchlistArgs(Args):
    action: Literal["list", "add", "remove", "enable", "disable", "add_category", "remove_category", "sync"]
    pattern: str | None = Field(None, max_length=100, description="add/remove/enable/disable: 'example.com' "
                                "(domain + subdomains) or '*.example.com' (subdomains only); remove also "
                                "takes a rule id")
    category: str | None = Field(None, max_length=48, description="add: category name, created if new, e.g. "
                                 "social_media, video_streaming:netflix; add_category/remove_category: the name")
    alert: bool = Field(True, description="add: hits raise an alert, light the node LED and notify subscribers")
    label: str | None = Field(None, max_length=48, description="add_category: display name")


class LabelsArgs(Args):
    action: Literal["list", "set", "remove"]
    key: str | None = Field(None, max_length=64, description="MAC (preferred), IPv4 address or node id")
    label: str | None = Field(None, max_length=64)


NodeAction = Literal["ping", "identify", "reboot", "set_alert_led", "arp_scan", "flush_dns_cache",
                     "sync_watchlist", "network_stats", "local_devices", "dns_log"]


class NodeActionArgs(DeviceArgs):
    action: NodeAction
    on: bool | None = Field(None, description="set_alert_led: true = on")
    lease_s: float | None = Field(None, gt=0, le=86400, description="set_alert_led: the node switches the LED "
                                  "off by itself after this long")
    limit: int | None = Field(None, ge=1, le=128, description="local_devices / dns_log")
    flagged_only: bool | None = Field(None, description="dns_log")
    confirm: bool = Field(False, description="must be true for reboot / set_alert_led - ask the user first")


class SendCommandArgs(Args):
    device_id: str = Field(min_length=3, max_length=64, description="a fw 2.x board, or 'broadcast'")
    action: Literal["reboot", "identify", "ping", "set_meta", "set_display", "dns_probe", "set_state_mode",
                    "set_wifi"]
    ssid: str | None = Field(None, max_length=32)
    pass_: str | None = Field(None, alias="pass", max_length=63)
    mode: Literal["STANDBY", "ACTIVE", "INFERENCE_ACTIVE", "LOW_POWER_SLEEP", "FAILSAFE"] | None = None
    duration_s: int | None = Field(None, ge=5, le=86400)
    dns_server: str | None = Field(None, max_length=45)
    names: list[str] | None = Field(None, max_length=12)
    site: str | None = Field(None, max_length=32)
    group: str | None = Field(None, max_length=32)
    label: str | None = Field(None, max_length=48)
    driver: Literal["ssd1306", "sh1106", "off", "auto"] | None = None
    sda: int | None = Field(None, ge=0, le=63)
    scl: int | None = Field(None, ge=0, le=63)
    confirm: bool = Field(False, description="must be true for reboot, set_wifi, set_state_mode and broadcast")
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class CommandResultsArgs(Args):
    device_id: str | None = Field(None, max_length=64)
    cmd_id: str | None = Field(None, max_length=64)
    limit: int = Field(20, ge=1, le=200)


class ForgetArgs(DeviceArgs):
    confirm: bool = Field(False, description="must be true - deletes every stored row of the device")


class DeviceCallArgs(DeviceArgs):
    tool: str = Field(min_length=3, max_length=40, description="the node's own tool, see device_list_tools")
    arguments: dict[str, Any] = Field(default_factory=dict, description="field names of that tool's arguments")
    timeout_s: int = Field(8, ge=1, le=30)
    confirm: bool = Field(False, description="must be true for tools that change the node")


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
@dataclass
class Ctx:
    registry: Any
    store: Any
    hub: Any = None
    pipeline: Any = None
    rules: Any = None
    labels: Any = None
    dns: Any = None
    integrations: dict = field(default_factory=dict)
    authorized: bool = True


Handler = Callable[[Any, Ctx], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    title: str
    description: str
    model: type[Args]
    handler: Handler
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    open_world: bool = False
    admin: Callable[[Any], bool] | bool = False

    def definition(self) -> dict[str, Any]:
        schema = self.model.model_json_schema(by_alias=True)
        schema.pop("title", None)
        schema.setdefault("properties", {})
        return {"name": self.name, "title": self.title, "description": self.description, "inputSchema": schema,
                "annotations": {"title": self.title, "readOnlyHint": self.read_only,
                                "destructiveHint": self.destructive, "idempotentHint": self.idempotent,
                                "openWorldHint": self.open_world}}

    def needs_admin(self, args: Any) -> bool:
        return self.admin(args) if callable(self.admin) else bool(self.admin)


TOOLS: dict[str, Tool] = {}


def tool(name: str, title: str, description: str, model: type[Args], **kw):
    def deco(fn: Handler) -> Handler:
        TOOLS[name] = Tool(name, title, " ".join(description.split()), model, fn, **kw)
        return fn
    return deco


def _need(ctx: Ctx, attr: str) -> Any:
    v = getattr(ctx, attr)
    if v is None:
        raise ToolFailure(f"this server runs without the {attr} component")
    return v


def _device(ctx: Ctx, did: str) -> dict:
    d = ctx.registry.devices.get(did)
    if d is None:
        raise ToolFailure(f"unknown device '{did}' - list_devices shows the known ones")
    return d


def _node_row(v) -> dict[str, Any]:
    m = v.metrics or {}
    return {"device_id": v.device_id, "label": v.label, "kind": v.kind, "protocol": v.protocol,
            "health": v.health, "ip": v.ip, "mac": v.mac, "rssi_dbm": m.get("rssi_dbm"),
            "free_heap_bytes": m.get("free_heap_bytes"), "temp_c": m.get("temp_c"), "uptime_s": v.uptime_s,
            "fw": v.fw, "site": v.site, "group": v.group, "transport": v.transport,
            "last_seen": iso(v.last_seen), "last_seen_ts": v.last_seen, "last_seen_age_s": v.last_seen_age_s,
            "dns_forwarder": (v.dns or {}).get("mode") == P.DNS_MODE_FORWARD if v.dns else False,
            "dns_queries": (v.dns or {}).get("queries") if v.dns else None,
            "watchlist_version": v.watchlist_version, "alerts": v.active_alerts, "mode": v.mode}


# ---- fleet ---------------------------------------------------------------------------------
@tool("fleet_summary", "Fleet summary",
      """Health of every node in one call: total/online/stale/offline/alerting, breakdown by site, group and
      firmware, ingest rate, open alerts. Start here.""", NoArgs)
async def _fleet_summary(a: NoArgs, ctx: Ctx):
    return ctx.registry.summary().model_dump()


@tool("list_devices", "List devices",
      """All paired nodes (ESP32 firmware 2.x/3.x, Raspberry Pi agents, Pico boards) with live status, IP, MAC,
      RSSI, free heap, uptime, firmware, last-seen time and whether the node forwards LAN DNS. Filter by site,
      group, health, kind or free text; page with limit/offset.""", ListDevicesArgs)
async def _list_devices(a: ListDevicesArgs, ctx: Ctx):
    views = ctx.registry.list_views(site=a.site, group=a.group, health=a.health, search=a.search,
                                    limit=10_000, offset=0)
    if a.kind:
        views = [v for v in views if v.kind == a.kind]
    page = views[a.offset:a.offset + a.limit]
    return {"total": len(views), "count": len(page), "offset": a.offset,
            "next_offset": a.offset + a.limit if a.offset + a.limit < len(views) else None,
            "devices": [_node_row(v) for v in page]}


@tool("get_device", "One device", "Full current state of one device plus its recent telemetry rows.", GetDeviceArgs)
async def _get_device(a: GetDeviceArgs, ctx: Ctx):
    _device(ctx, a.device_id)
    view = ctx.registry.device_view(a.device_id)
    history = await ctx.store.query_telemetry(device_id=a.device_id, limit=a.history_points) \
        if a.history_points else []
    return {"device": view.model_dump(), "history": history}


_KEY_METRICS = ("free_heap_bytes", "min_free_heap_bytes", "largest_free_block_bytes", "heap_fragmentation",
                "rssi_dbm", "cpu_pct", "temp_c", "tx_fail", "reconnects", "dns_queries", "dns_flagged",
                "dns_upstream_fail", "dns_clients", "mem_used_pct", "load_1m")


@tool("get_device_telemetry", "Device telemetry",
      """Hardware and network metrics of one node over a time window: per-metric series (newest first) with
      min/max/mean, plus the latest snapshot. Default metrics: heap (free, minimum, largest block,
      fragmentation), RSSI, CPU, temperature, publish failures, reconnects and the DNS forwarder counters.""",
      DeviceTelemetryArgs)
async def _device_telemetry(a: DeviceTelemetryArgs, ctx: Ctx):
    d = _device(ctx, a.device_id)
    since = time.time() - a.minutes * 60
    latest = dict(d.get("metrics") or {})
    names = a.metrics or [m for m in _KEY_METRICS if m in latest] or sorted(latest)[:12]
    series = {}
    for name in names:
        rows = await ctx.store.query_telemetry(device_id=a.device_id, metric=name, since_s=since,
                                               limit=a.max_points)
        vals = [r["value"] for r in rows if r.get("value") is not None]
        series[name] = {"points": len(rows),
                        "min": min(vals) if vals else None, "max": max(vals) if vals else None,
                        "mean": round(sum(vals) / len(vals), 3) if vals else None,
                        "latest": latest.get(name),
                        "series": [[round(r["ts"], 1), r["value"]] for r in rows]}
    view = ctx.registry.device_view(a.device_id)
    return {"device_id": a.device_id, "label": view.label, "health": view.health, "minutes": a.minutes,
            "last_seen": iso(view.last_seen), "uptime_s": view.uptime_s, "fw": view.fw, "metrics": series,
            "latest": latest, "dns": d.get("dns")}


@tool("query_telemetry", "Query a metric",
      """Raw time series of one metric (temp_c, rssi_dbm, free_heap_bytes, dns_queries ...) across the fleet or
      scoped to a device, site or group.""", QueryTelemetryArgs)
async def _query_telemetry(a: QueryTelemetryArgs, ctx: Ctx):
    rows = await ctx.store.query_telemetry(device_id=a.device_id, site=a.site, group=a.group, metric=a.metric,
                                           since_s=time.time() - a.minutes * 60, limit=a.limit)
    return {"metric": a.metric, "points": len(rows), "rows": rows}


@tool("aggregate_metric", "Aggregate a metric",
      "Fleet-wide count/mean/min/max of one metric over a window plus the ten devices with the highest mean.",
      AggregateArgs)
async def _aggregate(a: AggregateArgs, ctx: Ctx):
    return await ctx.store.aggregate_metric(a.metric, time.time() - a.minutes * 60, site=a.site, group=a.group)


@tool("get_alerts", "Alerts",
      "Open (or historical) alerts: node health (RSSI, heap, temperature) and LAN watchlist hits (lan-<ip>).",
      AlertsArgs)
async def _alerts(a: AlertsArgs, ctx: Ctx):
    return {"alerts": await ctx.store.list_alerts(device_id=a.device_id, open_only=a.open_only, limit=a.limit)}


# ---- LAN DNS ------------------------------------------------------------------------------
@tool("query_dns_activity", "Query LAN DNS activity",
      """Search the LAN DNS log (every lookup the Ionity nodes / resolver saw) by domain pattern, client IP,
      client MAC, device label, category, node, flagged-only and a time range; newest first, paginated with
      offset/next_offset. Each row: when, which client (IP, MAC, host name, label), what name and type,
      result, latency, repeat count, watchlist category.""", DnsActivityArgs)
async def _query_dns(a: DnsActivityArgs, ctx: Ctx):
    since = a.since if a.since is not None else (time.time() - a.minutes * 60 if a.minutes else None)
    from app.labels import norm_mac
    mac = None
    if a.client_mac:
        mac = norm_mac(a.client_mac)
        if not mac:
            raise ToolArgError("client_mac must look like aa:bb:cc:dd:ee:ff")
    out = await ctx.store.query_dns(domain_pattern=a.domain_pattern, client_ip=a.client_ip, mac=mac,
                                    label=a.device_label, category=a.category, node_id=a.node_id,
                                    flagged_only=a.flagged_only, since=since, until=a.until,
                                    limit=a.limit, offset=a.offset)
    for r in out["queries"]:
        r["time"] = iso(r["ts"])
        r["flagged"] = bool(r["flagged"])
        r["cached"] = bool(r["cached"])
    out["filters"] = {k: v for k, v in a.model_dump().items() if v not in (None, False) and k not in ("limit", "offset")}
    return out


@tool("get_flagged_alerts", "Flagged activity",
      """Watchlist hits - e.g. 'who opened YouTube in the last 30 minutes?' - with client attribution (label,
      IP, MAC, host name), the node that saw it, first/last time, hit counts and whether the episode is still
      active. group_by=client (default) gives one row per device and category with total hits;
      group_by=event lists every episode.""", FlaggedArgs)
async def _flagged(a: FlaggedArgs, ctx: Ctx):
    rows = await ctx.store.list_flags(since=time.time() - a.minutes * 60, category=a.category,
                                      client_ip=a.client_ip, label=a.device_label,
                                      active_within_s=settings.watch_hold_s if a.active_only else None,
                                      limit=1000 if a.group_by == "client" else a.limit)
    now = time.time()
    for r in rows:
        r["active"] = now - r["last_ts"] <= settings.watch_hold_s
        r["first_seen"], r["last_seen"] = iso(r["first_ts"]), iso(r["last_ts"])
    if a.group_by == "event":
        return {"minutes": a.minutes, "events": rows, "total_hits": sum(r["hits"] for r in rows)}
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        k = (r["client_ip"], r["category"])
        g = agg.setdefault(k, {"client_ip": r["client_ip"], "mac": r["mac"], "hostname": r["hostname"],
                               "label": r["label"], "category": r["category"], "hits": 0, "episodes": 0,
                               "first_ts": r["first_ts"], "last_ts": r["last_ts"], "domains": set(),
                               "nodes": set(), "active": False})
        g["hits"] += r["hits"]
        g["episodes"] += 1
        g["first_ts"], g["last_ts"] = min(g["first_ts"], r["first_ts"]), max(g["last_ts"], r["last_ts"])
        g["domains"].add(r["last_qname"])
        if r.get("node_id"):
            g["nodes"].add(r["node_id"])
        g["active"] = g["active"] or r["active"]
        for f in ("label", "mac", "hostname"):
            g[f] = g[f] or r[f]
    clients = sorted(agg.values(), key=lambda g: -g["last_ts"])[: a.limit]
    for g in clients:
        g["domains"], g["nodes"] = sorted(g["domains"]), sorted(g["nodes"])
        g["first_seen"], g["last_seen"] = iso(g["first_ts"]), iso(g["last_ts"])
        g["who"] = g["label"] or g["hostname"] or g["client_ip"]
    return {"minutes": a.minutes, "clients": clients, "total_hits": sum(g["hits"] for g in clients),
            "active_clients": sum(1 for g in clients if g["active"])}


@tool("dns_summary", "LAN DNS summary",
      "Queries, distinct domains, clients, cache hits, flagged hits and per-category counts over a window, plus "
      "the state of every DNS source (nodes and the host resolver).", DnsWindowArgs)
async def _dns_summary(a: DnsWindowArgs, ctx: Ctx):
    out = await ctx.store.dns_summary(time.time() - a.minutes * 60)
    out["minutes"] = a.minutes
    out["host_resolver"] = ctx.dns.stats() if ctx.dns else {"running": False}
    out["nodes"] = [{"device_id": did, "label": d.get("label"), "dns": d.get("dns")}
                    for did, d in ctx.registry.devices.items() if d.get("dns")]
    return out


@tool("dns_by_device", "DNS by device",
      "What's going to what device: per client its label, MAC, query count, flagged hits and top domains.",
      DnsWindowArgs)
async def _dns_by_device(a: DnsWindowArgs, ctx: Ctx):
    return {"minutes": a.minutes, "devices": await ctx.store.dns_by_device(time.time() - a.minutes * 60, a.limit)}


@tool("dns_top_domains", "Top domains", "Busiest domains in a window, optionally for one client.", TopDomainsArgs)
async def _top_domains(a: TopDomainsArgs, ctx: Ctx):
    return {"minutes": a.minutes,
            "domains": await ctx.store.dns_top_domains(time.time() - a.minutes * 60, a.limit, a.client_ip)}


@tool("list_lan_devices", "LAN devices",
      "Every client seen on the LAN (DNS, ARP, DHCP, mDNS): IP, MAC, vendor, host name, label, which node saw it.",
      LanDevicesArgs)
async def _lan_devices(a: LanDevicesArgs, ctx: Ctx):
    return {"devices": await ctx.store.list_lan_devices(a.limit)}


# ---- management ------------------------------------------------------------------------------
@tool("manage_dns_watchlist", "Manage the DNS watchlist",
      """List, add, remove, enable or disable flagged-domain rules and their categories (built-in: YouTube,
      TikTok; add your own, e.g. category social_media with facebook.com, instagram.com). Every change is
      pushed to all connected ESP32 / Pi nodes so they flag and light their alert LED locally. Built-in rules
      can be disabled, not deleted. Changes need the admin token.""", WatchlistArgs,
      read_only=False, idempotent=True, admin=lambda a: a.action != "list")
async def _watchlist(a: WatchlistArgs, ctx: Ctx):
    from app.rules.engine import RuleError
    rules = _need(ctx, "rules")
    try:
        if a.action == "list":
            return rules.snapshot()
        if a.action == "sync":
            return {"version": rules.version, "nodes": await _need(ctx, "hub").sync_all()}
        if a.action in ("add_category", "remove_category"):
            if not a.category:
                raise ToolArgError(f"{a.action} needs category")
            if a.action == "add_category":
                c = await rules.add_category(a.category, a.label)
                return {"added_category": {"id": c.id, "name": c.name, "label": c.label}, "version": rules.version}
            return {**await rules.remove_category(a.category), "version": rules.version}
        if not a.pattern:
            raise ToolArgError(f"{a.action} needs pattern")
        if a.action == "add":
            if not a.category:
                raise ToolArgError("add needs category (e.g. social_media)")
            r = await rules.add_rule(a.pattern, a.category, a.alert)
            return {"added": rules.rule_view(r), "version": rules.version}
        if a.action == "remove":
            return {**await rules.remove_rule(a.pattern), "version": rules.version}
        r = await rules.set_enabled(a.pattern, a.action == "enable")
        return {"rule": rules.rule_view(r), "version": rules.version}
    except RuleError as e:
        raise ToolArgError(str(e)) from None


@tool("manage_device_labels", "Manage device labels",
      """View or change friendly names of LAN clients and nodes in config/device_labels.json. Key by MAC
      (preferred - survives DHCP changes), IPv4 address or node id. list also shows discovered clients that
      have no label yet. Changes apply to the next DNS query without a restart and need the admin token.""",
      LabelsArgs, read_only=False, admin=lambda a: a.action != "list")
async def _labels(a: LabelsArgs, ctx: Ctx):
    book = _need(ctx, "labels")
    if a.action == "list":
        lan = await ctx.store.list_lan_devices(300)
        return {"file": str(book.path), "error": book.error, "entries": book.entries(),
                "unlabelled": [{"ip": d["ip"], "mac": d.get("mac"), "hostname": d.get("hostname"),
                                "vendor": d.get("vendor"), "last_seen": iso(d.get("last_seen"))}
                               for d in lan if not d.get("label")][:100]}
    if not a.key:
        raise ToolArgError(f"{a.action} needs key (a MAC, IPv4 address or node id)")
    try:
        if a.action == "set":
            if not a.label:
                raise ToolArgError("set needs label")
            return {"set": book.set(a.key, a.label)}
        if not book.remove(a.key):
            raise ToolFailure(f"no label for '{a.key}'")
        return {"removed": a.key}
    except ValueError as e:
        raise ToolArgError(str(e)) from None


@tool("trigger_node_action", "Run an action on a node",
      """Execute a command on an ESP32 / Pi node: ping (round trip), identify (blink), reboot, set_alert_led
      (on/off, optional lease_s), arp_scan (sweep its /24 so silent devices show up), flush_dns_cache,
      sync_watchlist, network_stats, local_devices, dns_log. reboot and set_alert_led change hardware: ask the
      user, then call with confirm=true. Changing actions need the admin token.""", NodeActionArgs,
      read_only=False, destructive=True, idempotent=False, open_world=True,
      admin=lambda a: a.action not in ("ping", "network_stats", "local_devices", "dns_log"))
async def _node_action(a: NodeActionArgs, ctx: Ctx):
    from app.fleet.actions import ActionError, node_action
    _device(ctx, a.device_id)
    if a.action in ("reboot", "set_alert_led") and not a.confirm:
        raise ToolArgError(f"'{a.action}' changes hardware on {a.device_id}: ask the user, then call again with "
                           "confirm=true")
    params = {k: v for k, v in (("on", a.on), ("lease_s", a.lease_s), ("limit", a.limit),
                                ("flagged_only", a.flagged_only)) if v is not None}
    try:
        r = await node_action(a.device_id, a.action, params, registry=ctx.registry, hub=ctx.hub)
    except ActionError as e:
        raise ToolArgError(str(e)) from None
    if r.get("ok") is False:
        raise ToolFailure(r.get("error") or f"{a.action} failed")
    return r


# ---- legacy commands + node tools -------------------------------------------------------------
@tool("send_command", "Send a fw 2.x command",
      """Commands for fw 2.x JSON boards (or 'broadcast'): reboot, identify, ping, set_meta, set_display,
      dns_probe, set_state_mode, set_wifi. Replies arrive asynchronously - read them with get_command_results.
      For firmware 3 / Pi nodes use trigger_node_action. reboot, set_wifi, set_state_mode and broadcast need
      confirm=true (ask the user first) and the admin token.""", SendCommandArgs,
      read_only=False, destructive=True, idempotent=False, open_world=True, admin=True)
async def _send_command(a: SendCommandArgs, ctx: Ctx):
    if (a.action in ("reboot", "set_wifi", "set_state_mode") or a.device_id == "broadcast") and not a.confirm:
        raise ToolArgError(f"'{a.action}' on {a.device_id} needs confirm=true - ask the user first")
    if a.device_id != "broadcast":
        d = _device(ctx, a.device_id)
        if d.get("protocol") == "ewp1":
            raise ToolArgError(f"{a.device_id} speaks EWP/1 (firmware 3 / Pi agent) - use trigger_node_action")
    payload = {k: v for k, v in a.model_dump(by_alias=True).items()
               if k in ("site", "group", "label", "driver", "sda", "scl", "dns_server", "names", "mode",
                        "duration_s", "ssid", "pass") and v is not None}
    if a.action == "set_state_mode" and not payload.get("mode"):
        raise ToolArgError("set_state_mode needs mode")
    if a.action == "set_wifi":
        if not payload.get("ssid"):
            raise ToolArgError("set_wifi needs ssid (and pass unless the network is open)")
        payload.setdefault("pass", "")
    if a.action == "dns_probe" and not (payload.get("names") or []):
        raise ToolArgError("dns_probe needs 1-12 names")
    if a.action == "set_meta" and not any(k in payload for k in ("site", "group", "label")):
        raise ToolArgError("set_meta needs at least one of site, group, label")
    r = await ctx.registry.send_command(a.device_id, a.action, payload)
    if not r.get("ok"):
        raise ToolFailure(r.get("error") or "the broker refused the command")
    return r


@tool("get_command_results", "Command results",
      "Replies nodes sent to commands and node events (lease expired, mode changed ...), newest first.",
      CommandResultsArgs)
async def _cmd_results(a: CommandResultsArgs, ctx: Ctx):
    return {"results": ctx.registry.list_cmd_results(device_id=a.device_id, cmd_id=a.cmd_id, limit=a.limit)}


@tool("forget_device", "Forget a device",
      """Remove a device from the fleet: live registry plus every stored row. Irreversible - ask the user, then
      call with confirm=true. A node that is still publishing simply re-registers.""", ForgetArgs,
      read_only=False, destructive=True, idempotent=True, admin=True)
async def _forget(a: ForgetArgs, ctx: Ctx):
    if not a.confirm:
        raise ToolArgError(f"forget_device deletes all history of {a.device_id}: ask the user, then confirm=true")
    r = await ctx.registry.forget_device(a.device_id)
    if not r.get("ok"):
        raise ToolFailure(r.get("error") or "unknown device")
    return r


@tool("device_list_tools", "A node's own tools",
      "The tools one node itself offers (EWP/1 nodes from their HELLO; fw 2.x boards asked over MQTT).",
      DeviceArgs, open_world=True)
async def _device_tools(a: DeviceArgs, ctx: Ctx):
    d = _device(ctx, a.device_id)
    if d.get("protocol") == "ewp1":
        return {"device_id": a.device_id, "protocol": "ewp1", "tools": d.get("tools") or [],
                "actuators": d.get("actuators") or []}
    r = await ctx.registry.call_device(a.device_id, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                       settings.device_rpc_timeout_s)
    if r.get("ok") and isinstance(r.get("response"), dict):
        tools = (r["response"].get("result") or {}).get("tools", [])
        return {"device_id": a.device_id, "protocol": "json", "count": len(tools), "tools": tools,
                "elapsed_ms": r.get("elapsed_ms")}
    raise ToolFailure(r.get("error") or "no answer from the board")


@tool("device_call_tool", "Call a node's own tool",
      """Call one tool on a node itself and return its answer. EWP/1 nodes: get_device_info, read_telemetry,
      get_watchlist, get_network_stats, get_local_devices, query_dns_log (read) and set_actuator, identify,
      scan_lan, flush_dns_cache, set_watchlist, reboot (write: confirm=true + admin token); arguments use the
      field names of packages/protocol (e.g. set_actuator {channel, value, leaseMs}). fw 2.x boards:
      read_telemetry, get_device_info, run_inference, set_actuator, set_state_mode, identify.""",
      DeviceCallArgs, read_only=False, destructive=True, idempotent=False, open_world=True,
      admin=lambda a: a.tool not in DEVICE_READ_TOOLS)
async def _device_call(a: DeviceCallArgs, ctx: Ctx):
    d = _device(ctx, a.device_id)
    if a.tool not in DEVICE_READ_TOOLS and not a.confirm:
        raise ToolArgError(f"'{a.tool}' changes {a.device_id}: ask the user, then call again with confirm=true")
    if d.get("protocol") == "ewp1":
        try:
            r = await _need(ctx, "hub").call(a.device_id, a.tool, a.arguments, timeout_s=a.timeout_s)
        except ValueError as e:
            raise ToolArgError(str(e)) from None
        except (KeyError, TypeError) as e:
            raise ToolArgError(f"argument names for {a.tool}: {e}") from None
        if not r.get("ok"):
            raise ToolFailure(r.get("error") or f"{a.tool} failed")
        return {"device_id": a.device_id, **r}
    r = await ctx.registry.call_device(a.device_id, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": a.tool, "arguments": a.arguments}}, float(a.timeout_s))
    resp = r.get("response")
    if not r.get("ok") or not isinstance(resp, dict):
        raise ToolFailure(r.get("error") or "no answer from the board")
    if "error" in resp:
        raise ToolFailure(resp["error"].get("message") or "board error")
    res = resp.get("result") or {}
    data = res.get("structuredContent")
    if data is None:
        import json as _json
        txt = (res.get("content") or [{}])[0].get("text", "")
        try:
            data = _json.loads(txt)
        except ValueError:
            data = {"text": txt}
    if res.get("isError"):
        raise ToolFailure(str(data))
    return {"ok": True, "device_id": a.device_id, "tool": a.tool, "result": data, "elapsed_ms": r.get("elapsed_ms")}


@tool("integrations_status", "Integrations",
      "Datadog forwarder, MQTT bridge, EWP/1 hub and DNS pipeline counters.", NoArgs)
async def _integrations(a: NoArgs, ctx: Ctx):
    out = {k: (v.stats() if hasattr(v, "stats") and callable(v.stats) else v) for k, v in ctx.integrations.items()}
    if ctx.hub:
        out["ewp"] = ctx.hub.stats
    if ctx.pipeline:
        out["dns_pipeline"] = ctx.pipeline.stats
    return out or {"datadog": {"enabled": False}}


# ---------------------------------------------------------------------------
# Public API used by the server
# ---------------------------------------------------------------------------
MCP_TOOLS = [t.definition() for t in TOOLS.values()]
WRITE_TOOLS = {n for n, t in TOOLS.items() if t.admin is True}


def needs_admin(name: str, args: Any) -> bool:
    t = TOOLS.get(name)
    if t is None:
        return False
    try:
        parsed = t.model.model_validate(args or {})
    except ValidationError:
        return t.admin is True or callable(t.admin)
    return t.needs_admin(parsed)


def validate_args(name: str, args: Any) -> Args:
    t = TOOLS.get(name)
    if t is None:
        raise KeyError(name)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolArgError("arguments must be a JSON object")
    try:
        return t.model.model_validate(args)
    except ValidationError as e:
        parts = []
        for err in e.errors(include_url=False):
            loc = ".".join(str(x) for x in err["loc"]) or "arguments"
            parts.append(f"{loc}: {err['msg']}")
        raise ToolArgError(f"{name}: invalid arguments - " + "; ".join(parts)) from None


async def execute(name: str, args: Any, ctx: Ctx) -> Any:
    parsed = validate_args(name, args)
    return await TOOLS[name].handler(parsed, ctx)
