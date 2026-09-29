"""
AEDI - IONITY GLOBAL | Datadog forwarder for the ESP32-MCP fleet
Doc ID: DOC-2026-09-ESP32MCP-DD | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

The fleet host is the only thing that talks to Datadog. Boards never hold an
API key: they report to the host over MQTT/HTTP as always, and this module
turns what the host already knows into Datadog data:

  metrics  every numeric telemetry field  -> ionity.esp32.<metric>   (gauge)
           fleet rollup every flush        -> ionity.fleet.devices{health:...},
                                              ionity.fleet.ingest_rate, ...
           tags: device_id, site, group, fw, transport, env, service
           resources: host = device_id, so each board is its own host in Datadog
  events   alert raised / cleared, board online / offline / sleeping
  checks   ionity.esp32.can_connect per board (OK / WARNING stale / CRITICAL offline)

Enable in .env:
    IONITY_DD_ENABLED=true
    IONITY_DD_API_KEY=<key>            # Organization settings -> API keys
    IONITY_DD_SITE=datadoghq.eu        # or datadoghq.com, us3/us5.datadoghq.com, ap1.datadoghq.com
    IONITY_DD_ENV=lab

Nothing is sent without a key; failures are counted and backed off, never
raised into the ingest path.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

import httpx

log = logging.getLogger("ionity.datadog")

MAX_SERIES_PER_POST = 500
MAX_QUEUE = 50_000


class DatadogForwarder:
    def __init__(self, settings, registry=None, client: httpx.AsyncClient | None = None):
        self.s = settings
        self.registry = registry
        self.enabled = bool(settings.dd_enabled and settings.dd_api_key)
        self.base = f"https://api.{settings.dd_site}"
        self._client = client
        self._points: deque[dict[str, Any]] = deque(maxlen=MAX_QUEUE)
        self._events: deque[dict[str, Any]] = deque(maxlen=2000)
        self._checks: dict[str, dict[str, Any]] = {}
        self._last_health: dict[str, str] = {}
        self._task: asyncio.Task | None = None
        self.sent_points = 0
        self.sent_events = 0
        self.sent_checks = 0
        self.errors = 0
        self.dropped = 0
        self.last_error: str | None = None
        self.last_flush: float | None = None
        self._backoff_until = 0.0

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if not self.enabled:
            why = "no IONITY_DD_API_KEY" if self.s.dd_enabled else "IONITY_DD_ENABLED=false"
            log.info("Datadog forwarder off (%s)", why)
            return
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        self._task = asyncio.create_task(self._loop())
        log.info("Datadog forwarder -> %s every %ss (env=%s)", self.base,
                 self.s.dd_flush_s, self.s.dd_env)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.enabled:
            try:
                await self.flush()
            except Exception:
                pass
        if self._client:
            await self._client.aclose()

    # -- hooks called by the registry (sync, O(1), never raise) -------------
    def _tags(self, device: dict[str, Any]) -> list[str]:
        t = [f"device_id:{device.get('device_id')}",
             f"site:{device.get('site') or 'unknown'}",
             f"group:{device.get('group') or 'default'}",
             f"env:{self.s.dd_env}", f"service:{self.s.dd_service}"]
        if device.get("fw"):
            t.append(f"fw:{device['fw']}")
        if device.get("transport"):
            t.append(f"transport:{device['transport']}")
        return t + [x.strip() for x in self.s.dd_tags.split(",") if x.strip()]

    def on_telemetry(self, device: dict[str, Any], metrics: dict[str, Any], ts: float) -> None:
        if not self.enabled:
            return
        tags = self._tags(device)
        did = device.get("device_id")
        for k, v in metrics.items():
            if isinstance(v, bool):
                v = 1.0 if v else 0.0
            if not isinstance(v, (int, float)) or v != v:        # skip str / NaN
                continue
            if len(self._points) == self._points.maxlen:
                self.dropped += 1
            self._points.append({"metric": f"{self.s.dd_metric_prefix}.{k}", "value": float(v),
                                 "ts": int(ts), "tags": tags, "host": did})

    def on_alert(self, device: dict[str, Any], code: str, message: str, severity: str,
                 value: float | None, raised: bool) -> None:
        if not self.enabled:
            return
        did = device.get("device_id")
        self._events.append({
            "title": f"[{'ALERT' if raised else 'CLEARED'}] {did}: {code}",
            "text": (message if raised else f"{code} cleared on {did}")
                    + (f"\nvalue: {value}" if value is not None else ""),
            "alert_type": ({"critical": "error", "warning": "warning"}.get(severity, "info")
                           if raised else "success"),
            "tags": self._tags(device) + [f"alert:{code}"],
            "host": did,
            "aggregation_key": f"{did}:{code}",
            "source_type_name": "ionity-esp32-mcp",
            "date_happened": int(time.time()),
        })

    def on_state(self, device: dict[str, Any], state: str, detail: str = "") -> None:
        """online / offline / sleeping transitions from status + LWT."""
        if not self.enabled:
            return
        did = device.get("device_id")
        if not state.startswith("inference:"):
            if self._last_health.get(did) == state:
                return
            self._last_health[did] = state
        self._events.append({
            "title": f"{did} is {state}",
            "text": detail or f"Ionity ESP32 board {did} reported {state}.",
            "alert_type": {"online": "success", "offline": "error"}.get(state, "info"),
            "tags": self._tags(device) + [f"state:{state}"],
            "host": did,
            "aggregation_key": f"{did}:state",
            "source_type_name": "ionity-esp32-mcp",
            "date_happened": int(time.time()),
        })

    # -- periodic --------------------------------------------------------
    def _collect_fleet(self, now: float) -> None:
        if not self.registry:
            return
        summary = self.registry.summary()
        base = [f"env:{self.s.dd_env}", f"service:{self.s.dd_service}"]
        for health in ("online", "stale", "offline", "alerting"):
            self._points.append({"metric": "ionity.fleet.devices", "value": float(getattr(summary, health)),
                                 "ts": int(now), "tags": base + [f"health:{health}"], "host": None})
        for name, val in (("ionity.fleet.devices.total", summary.total_devices),
                          ("ionity.fleet.ingest_rate", summary.ingest_rate_per_s),
                          ("ionity.fleet.alerts.open", summary.open_alerts)):
            self._points.append({"metric": name, "value": float(val), "ts": int(now),
                                 "tags": base, "host": None})
        for v in self.registry.list_views(limit=5000):
            status = {"online": 0, "alerting": 0, "stale": 1, "offline": 2}.get(v.health, 3)
            self._checks[v.device_id] = {
                "check": f"{self.s.dd_metric_prefix}.can_connect", "host_name": v.device_id,
                "status": status, "timestamp": int(now),
                "message": f"{v.health}; last seen {v.last_seen_age_s}s ago",
                "tags": self._tags({"device_id": v.device_id, "site": v.site, "group": v.group,
                                    "fw": v.fw, "transport": v.transport}),
            }

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.s.dd_flush_s)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.errors += 1
                self.last_error = str(e)
                log.warning("Datadog flush failed: %s", e)

    def _series_payload(self, pts: list[dict[str, Any]]) -> dict[str, Any]:
        series = []
        for p in pts:
            s = {"metric": p["metric"], "type": 3,          # 3 = gauge
                 "points": [{"timestamp": p["ts"], "value": p["value"]}],
                 "tags": p["tags"]}
            if p.get("host"):
                s["resources"] = [{"name": p["host"], "type": "host"}]
            series.append(s)
        return {"series": series}

    async def _post(self, path: str, body: Any) -> None:
        r = await self._client.post(self.base + path, json=body, headers={
            "DD-API-KEY": self.s.dd_api_key, "Content-Type": "application/json"})
        if r.status_code >= 300:
            raise RuntimeError(f"{path} -> HTTP {r.status_code}: {r.text[:200]}")

    async def flush(self) -> dict[str, int]:
        if not self.enabled or self._client is None:
            return {"points": 0, "events": 0, "checks": 0}
        now = time.time()
        if now < self._backoff_until:
            return {"points": 0, "events": 0, "checks": 0}
        self._collect_fleet(now)
        sent = {"points": 0, "events": 0, "checks": 0}
        try:
            while self._points:
                batch = [self._points.popleft() for _ in range(min(MAX_SERIES_PER_POST, len(self._points)))]
                try:
                    await self._post("/api/v2/series", self._series_payload(batch))
                except Exception:
                    self._points.extendleft(reversed(batch))           # retry next flush
                    raise
                sent["points"] += len(batch)
            while self._events:
                ev = self._events[0]
                await self._post("/api/v1/events", ev)
                self._events.popleft()
                sent["events"] += 1
            for did, chk in list(self._checks.items()):
                await self._post("/api/v1/check_run", chk)
                sent["checks"] += 1
            self._checks.clear()
        except Exception as e:
            self.errors += 1
            self.last_error = str(e)
            self._backoff_until = time.time() + min(300, 15 * (2 ** min(self.errors, 4)))
            log.warning("Datadog: %s (backing off)", e)
        self.sent_points += sent["points"]
        self.sent_events += sent["events"]
        self.sent_checks += sent["checks"]
        self.last_flush = now
        return sent

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "site": self.s.dd_site, "env": self.s.dd_env,
            "queued_points": len(self._points), "queued_events": len(self._events),
            "sent_points": self.sent_points, "sent_events": self.sent_events,
            "sent_checks": self.sent_checks, "errors": self.errors, "dropped": self.dropped,
            "last_error": self.last_error, "last_flush": self.last_flush,
        }
