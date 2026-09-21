"""
FleetRegistry - the hot path.

Every reading, from MQTT or HTTP, lands here. The registry keeps an in-memory
view of the whole fleet (cheap: ~1 KB/device, so 1000 devices ~ 1 MB) and
pushes durable writes onto a queue that a single writer task drains in
batches. That keeps ingest O(1) per message and the DB doing one commit per
batch instead of one per device.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, Callable, Awaitable

from app.config import settings
from app.models import TelemetryIn, StatusIn, DeviceView, Alert, FleetSummary
from app.storage.base import Store

log = logging.getLogger("ionity.fleet")


class FleetRegistry:
    def __init__(self, store: Store):
        self.store = store
        self.devices: dict[str, dict[str, Any]] = {}
        self.queue: asyncio.Queue[TelemetryIn] = asyncio.Queue(maxsize=20_000)
        self._recent_ts: deque[float] = deque(maxlen=20_000)
        self._writer_task: asyncio.Task | None = None
        self._pruner_task: asyncio.Task | None = None
        self.command_publisher: Callable[[str, str, dict], Awaitable[bool]] | None = None
        self.started_at = time.time()
        self.dropped = 0

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        for row in await self.store.list_devices():
            self.devices[row["device_id"]] = {
                "device_id": row["device_id"],
                "site": row["site"],
                "group": row["grp"],
                "label": row["label"],
                "fw": row["fw"],
                "ip": row["ip"],
                "transport": row["transport"] or "unknown",
                "last_seen": row["last_seen"],
                "uptime_s": None,
                "msg_count": row["msg_count"],
                "metrics": {},
                "alerts": set(),
            }
        log.info("Loaded %d known devices from store", len(self.devices))
        self._writer_task = asyncio.create_task(self._writer_loop())
        self._pruner_task = asyncio.create_task(self._pruner_loop())

    async def stop(self) -> None:
        for t in (self._writer_task, self._pruner_task):
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    # -- ingest ------------------------------------------------------------
    def ingest(self, t: TelemetryIn) -> None:
        """Non-blocking. Updates hot state immediately, queues the durable write."""
        now = time.time()
        d = self.devices.setdefault(t.device_id, {
            "device_id": t.device_id, "msg_count": 0, "alerts": set(), "metrics": {},
        })
        d.update({
            "site": t.site, "group": t.group,
            "label": t.label or d.get("label"),
            "fw": t.fw or d.get("fw"),
            "ip": t.net.ip or d.get("ip"),
            "transport": t.net.transport,
            "last_seen": now,
            "uptime_s": t.uptime_s,
            "metrics": dict(t.metrics),
        })
        d["msg_count"] = d.get("msg_count", 0) + 1
        self._recent_ts.append(now)

        try:
            self.queue.put_nowait(t)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped % 500 == 1:
                log.warning("Ingest queue full - dropped %d durable writes", self.dropped)

    def ingest_status(self, s: StatusIn) -> None:
        d = self.devices.setdefault(s.device_id, {
            "device_id": s.device_id, "msg_count": 0, "alerts": set(), "metrics": {},
        })
        d.update({
            "site": s.site, "group": s.group,
            "label": s.label or d.get("label"),
            "fw": s.fw or d.get("fw"),
            "ip": s.ip or d.get("ip"),
            "transport": s.transport or d.get("transport", "unknown"),
            "uptime_s": s.uptime_s,
        })
        if s.state == "offline":
            # Last Will fired: mark it stale-now rather than faking a heartbeat.
            d["last_seen"] = d.get("last_seen") or time.time()
            d["lwt_offline"] = True
        else:
            d["lwt_offline"] = False
            d["last_seen"] = time.time()

    # -- durable writer ----------------------------------------------------
    async def _writer_loop(self) -> None:
        BATCH, FLUSH_S = 200, 1.0
        while True:
            try:
                batch: list[TelemetryIn] = [await self.queue.get()]
                deadline = time.monotonic() + FLUSH_S
                while len(batch) < BATCH and time.monotonic() < deadline:
                    try:
                        timeout = max(0.01, deadline - time.monotonic())
                        batch.append(await asyncio.wait_for(self.queue.get(), timeout))
                    except asyncio.TimeoutError:
                        break

                for t in batch:
                    await self.store.upsert_device(t)
                    await self.store.insert_telemetry(t)
                    await self._evaluate_alerts(t)
                await self.store.commit()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("writer loop error")
                await asyncio.sleep(1.0)

    async def _pruner_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(3600)
                cutoff = time.time() - settings.retention_days * 86400
                n = await self.store.prune(cutoff)
                if n:
                    log.info("Pruned %d rows older than %d days", n, settings.retention_days)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("pruner error")

    # -- alert engine ------------------------------------------------------
    async def _evaluate_alerts(self, t: TelemetryIn) -> None:
        now = t.at()
        m = t.metrics
        checks = [
            ("low_rssi", "warning", "rssi_dbm",
             lambda v: v < settings.alert_rssi_dbm,
             f"WiFi signal below {settings.alert_rssi_dbm} dBm"),
            ("packet_loss", "critical", "packet_loss_pct",
             lambda v: v > settings.alert_packet_loss_pct,
             f"Packet loss above {settings.alert_packet_loss_pct}%"),
            ("over_temp", "critical", "temp_c",
             lambda v: v > settings.alert_temp_c,
             f"Chip temperature above {settings.alert_temp_c} C"),
            ("low_heap", "warning", "free_heap_bytes",
             lambda v: v < settings.alert_free_heap_bytes,
             f"Free heap below {settings.alert_free_heap_bytes} bytes"),
        ]
        d = self.devices.get(t.device_id, {})
        active: set = d.setdefault("alerts", set())

        for code, sev, key, bad, msg in checks:
            raw = m.get(key)
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                continue
            v = float(raw)
            if bad(v):
                if code not in active:
                    await self.store.raise_alert(Alert(
                        device_id=t.device_id, severity=sev, code=code,
                        message=f"{msg} (={v})", value=v, raised_at=now,
                    ))
                    active.add(code)
            elif code in active:
                await self.store.clear_alert(t.device_id, code, now)
                active.discard(code)

    # -- views -------------------------------------------------------------
    def _health(self, d: dict) -> str:
        last = d.get("last_seen")
        if last is None:
            return "offline"
        age = time.time() - last
        if d.get("lwt_offline") or age > settings.offline_after_s:
            return "offline"
        if age > settings.stale_after_s:
            return "stale"
        return "alerting" if d.get("alerts") else "online"

    def device_view(self, device_id: str) -> DeviceView | None:
        d = self.devices.get(device_id)
        if not d:
            return None
        last = d.get("last_seen")
        return DeviceView(
            device_id=d["device_id"], site=d.get("site", "?"),
            group=d.get("group", "default"), label=d.get("label"),
            fw=d.get("fw"), ip=d.get("ip"),
            transport=d.get("transport", "unknown"),
            health=self._health(d), last_seen=last,
            last_seen_age_s=round(time.time() - last, 1) if last else None,
            uptime_s=d.get("uptime_s"), msg_count=d.get("msg_count", 0),
            metrics=d.get("metrics", {}),
            active_alerts=sorted(d.get("alerts", set())),
        )

    def list_views(self, site=None, group=None, health=None,
                   search=None, limit=2000, offset=0) -> list[DeviceView]:
        out: list[DeviceView] = []
        for did in self.devices:
            v = self.device_view(did)
            if not v:
                continue
            if site and v.site != site:
                continue
            if group and v.group != group:
                continue
            if health and v.health != health:
                continue
            if search and search.lower() not in f"{v.device_id} {v.label or ''} {v.ip or ''}".lower():
                continue
            out.append(v)
        out.sort(key=lambda v: ({"alerting": 0, "offline": 1, "stale": 2, "online": 3}[v.health], v.device_id))
        return out[offset: offset + limit]

    def summary(self) -> FleetSummary:
        counts = {"online": 0, "stale": 0, "offline": 0, "alerting": 0}
        sites: dict[str, int] = {}
        groups: dict[str, int] = {}
        firmware: dict[str, int] = {}
        for d in self.devices.values():
            counts[self._health(d)] += 1
            sites[d.get("site", "?")] = sites.get(d.get("site", "?"), 0) + 1
            groups[d.get("group", "default")] = groups.get(d.get("group", "default"), 0) + 1
            fw = d.get("fw") or "unknown"
            firmware[fw] = firmware.get(fw, 0) + 1

        now = time.time()
        last_min = sum(1 for t in self._recent_ts if now - t <= 60)
        open_alerts = sum(len(d.get("alerts", set())) for d in self.devices.values())

        return FleetSummary(
            fleet_name=settings.fleet_name,
            total_devices=len(self.devices),
            online=counts["online"], stale=counts["stale"],
            offline=counts["offline"], alerting=counts["alerting"],
            sites=sites, groups=groups, firmware=firmware,
            messages_last_minute=last_min,
            ingest_rate_per_s=round(last_min / 60.0, 2),
            open_alerts=open_alerts,
            generated_at=now,
        )

    def dashboard_payload(self, limit: int = 400) -> dict[str, Any]:
        """Compact frame for the WebSocket. Caps the device list so a
        1000-device fleet does not push megabytes per tick."""
        s = self.summary()
        views = self.list_views(limit=limit)
        return {
            "type": "fleet_tick",
            "summary": s.model_dump(),
            "devices": [v.model_dump() for v in views],
            "truncated": len(self.devices) > limit,
            "queue_depth": self.queue.qsize(),
            "dropped_writes": self.dropped,
        }

    # -- outbound commands -------------------------------------------------
    async def send_command(self, device_id: str, action: str, payload: dict) -> dict:
        if device_id not in self.devices and device_id != "broadcast":
            return {"ok": False, "error": f"unknown device '{device_id}'"}
        if not self.command_publisher:
            return {"ok": False, "error": "MQTT publisher unavailable - commands need the broker"}
        body = {"action": action, "cmd_id": f"c{int(time.time()*1000)}", **payload}
        ok = await self.command_publisher(device_id, action, body)
        await self.store.log_command(device_id, action, json.dumps(body))
        return {"ok": ok, "device_id": device_id, "command": body}
