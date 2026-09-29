"""
AEDI - IONITY GLOBAL | ESP32-MCP telemetry contract.
The `metrics` map is intentionally open: firmware can add a new metric and it
is stored, charted and MCP-queryable with no server change.
"""
from __future__ import annotations

import time
from typing import Any, Literal
from pydantic import BaseModel, Field


class NetInfo(BaseModel):
    ip: str | None = None
    transport: Literal["mqtt", "http", "serial", "sim", "unknown"] = "unknown"
    server: str | None = None
    resolved_by: str | None = None
    mcp: str | None = None            # fw >= 2.0: http://<board>/mcp


class TelemetryIn(BaseModel):
    device_id: str = Field(min_length=3, max_length=64)
    site: str = "ionity-local"
    group: str = "default"
    label: str | None = None
    fw: str | None = None
    product: str | None = None
    uptime_s: int | None = None
    seq: int | None = None
    metrics: dict[str, float | int | bool | str] = Field(default_factory=dict)
    net: NetInfo = Field(default_factory=NetInfo)
    ts: float | None = None          # server fills if device has no RTC
    mode: str | None = None          # fw >= 2.0 state mode

    def at(self) -> float:
        return self.ts if self.ts else time.time()


class StatusIn(BaseModel):
    device_id: str
    site: str = "ionity-local"
    group: str = "default"
    label: str | None = None
    fw: str | None = None
    state: Literal["online", "offline", "sleeping"] = "online"
    mode: str | None = None
    sleep_s: int | None = None
    mcp: bool | None = None
    ip: str | None = None
    uptime_s: int | None = None
    tx_ok: int | None = None
    tx_fail: int | None = None
    transport: str | None = None


class DeviceView(BaseModel):
    device_id: str
    site: str
    group: str
    label: str | None = None
    fw: str | None = None
    ip: str | None = None
    transport: str = "unknown"
    health: Literal["online", "stale", "offline", "alerting"] = "offline"
    last_seen: float | None = None
    last_seen_age_s: float | None = None
    uptime_s: int | None = None
    msg_count: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    active_alerts: list[str] = Field(default_factory=list)
    mode: str | None = None               # ACTIVE / STANDBY / INFERENCE_ACTIVE / FAILSAFE ...
    sleeping_until: float | None = None   # set while the board is in LOW_POWER_SLEEP
    mcp_url: str | None = None            # the board's own MCP endpoint (fw >= 2.0)


class Alert(BaseModel):
    id: int | None = None
    device_id: str
    severity: Literal["info", "warning", "critical"] = "warning"
    code: str
    message: str
    value: float | None = None
    raised_at: float
    cleared_at: float | None = None


class CommandIn(BaseModel):
    action: Literal["reboot", "identify", "ping", "set_meta", "set_display", "dns_probe",
                    "set_state_mode"]
    mode: Literal["STANDBY", "ACTIVE", "INFERENCE_ACTIVE", "LOW_POWER_SLEEP", "FAILSAFE"] | None = None
    duration_s: int | None = Field(default=None, ge=5, le=86400)
    # dns_probe: ask a resolver (default: Gate^Flame at the node's configured
    # gf_dns, 192.168.124.3 in the lab) for each name; the device reports what
    # came back - an address, 0.0.0.0 (blocked), NXDOMAIN, SERVFAIL or TIMEOUT.
    dns_server: str | None = None
    names: list[str] | None = Field(default=None, max_length=12)
    site: str | None = None
    group: str | None = None
    label: str | None = None
    # set_display: driver = ssd1306 | sh1106 | off | auto; sda/scl pin the bus
    driver: Literal["ssd1306", "sh1106", "off", "auto"] | None = None
    sda: int | None = None
    scl: int | None = None


class FleetSummary(BaseModel):
    fleet_name: str
    total_devices: int
    online: int
    stale: int
    offline: int
    alerting: int
    sites: dict[str, int]
    groups: dict[str, int]
    firmware: dict[str, int]
    messages_last_minute: int
    ingest_rate_per_s: float
    open_alerts: int
    generated_at: float
