"""
AEDI - IONITY GLOBAL | Network-wide alerts (watched sites, new devices)
Doc ID: DOC-2026-10-ESP32MCP-NETALERT | Policy 986 AED
Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd | AEDI
Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
Owner: github.com/Ionity-Global-Pty-Ltd | www.ionity.today | ai@ionity.today

Before v2.1 the dashboard's "Open alerts" only ever held board health alerts
(RSSI, heap, temperature) from the ESP32s themselves, and the YouTube alarm
lived in a separate script. This module runs inside the fleet server and turns
LAN activity from ANY device into ordinary fleet alerts:

  * watch:<domain>  a device on the network looked up a watched site
                    (default YouTube + TikTok). Clears after watch_hold_s quiet.
  * lan:new         a device that was not on the network before has joined.
                    Clears after new_device_hold_s.

It also drives the red light on an ESP32 (set_actuator over the board's own
MCP server) while any watched site is active - no extra script needed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from app.models import Alert

log = logging.getLogger("ionity.netwatch")


class NetworkWatch:
    def __init__(self, store, registry, settings):
        self.store = store
        self.registry = registry
        self.s = settings
        self.watch = [w.strip().lower().rstrip(".") for w in settings.watch_domains.split(",") if w.strip()]
        self.ignore = {x.strip() for x in settings.watch_ignore.split(",") if x.strip()}
        self.active: dict[tuple[str, str], float] = {}        # (ip, watched) -> last hit
        self.new_devices: dict[str, float] = {}                # ip -> raised at
        self.events: deque[dict[str, Any]] = deque(maxlen=300)
        self.labels: dict[str, str] = {}
        self.light_on = False
        self.light_device: str | None = None
        self.last_hit = 0.0
        self.light_error: str | None = None
        self._task: asyncio.Task | None = None
        self._busy: set[asyncio.Task] = set()

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        log.info("Network watch: %s (hold %ss), alarm board %s", ",".join(self.watch),
                 self.s.watch_hold_s, self.s.alarm_device or "auto (first online ESP32)")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.light_on:
            await self._set_light(False)

    # -- inputs ------------------------------------------------------------
    def matched(self, qname: str) -> str | None:
        q = (qname or "").lower().rstrip(".")
        return next((w for w in self.watch if q == w or q.endswith("." + w)), None)

    def on_query(self, row: dict[str, Any]) -> None:
        """Called by the DNS resolver for every logged query (event-loop thread)."""
        ip = row.get("client_ip") or "?"
        w = self.matched(row.get("qname", ""))
        if not w or ip in self.ignore:
            return
        self._spawn(self._hit(ip, row["qname"], w, row.get("ts") or time.time()))

    def on_new_device(self, ip: str, mac: str | None, name: str | None) -> None:
        """Called by the LAN sweep the first time an address shows up."""
        if ip in self.ignore:
            return
        self._spawn(self._new_device(ip, mac, name))

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._busy.add(t)
        t.add_done_callback(self._busy.discard)

    async def _label(self, ip: str) -> str:
        if ip not in self.labels:
            try:
                for d in await self.store.list_lan_devices(1000):
                    self.labels[d["ip"]] = d.get("label") or d.get("hostname") or d.get("vendor") or d["ip"]
            except Exception:
                pass
        return self.labels.get(ip) or ip

    async def _hit(self, ip: str, qname: str, w: str, ts: float) -> None:
        key = (ip, w)
        fresh = key not in self.active
        self.active[key] = time.time()
        self.last_hit = time.time()
        label = await self._label(ip)
        if fresh:
            await self.store.raise_alert(Alert(
                device_id=f"lan-{ip}", severity="warning", code=f"watch:{w}",
                message=f"{label} ({ip}) opened {qname}", raised_at=ts))
            self.events.append({"ts": ts, "ip": ip, "label": label, "qname": qname,
                                "watch": w, "kind": "watch"})
            log.info("ALERT  %s (%s) opened %s  [watch %s]", label, ip, qname, w)
        if not self.light_on:
            await self._set_light(True)

    async def _new_device(self, ip: str, mac: str | None, name: str | None) -> None:
        self.labels.pop(ip, None)
        label = name or ip
        self.new_devices[ip] = time.time()
        await self.store.raise_alert(Alert(
            device_id=f"lan-{ip}", severity="info", code="lan:new",
            message=f"New device on the network: {label} ({ip}{', ' + mac if mac else ''})",
            raised_at=time.time()))
        self.events.append({"ts": time.time(), "ip": ip, "label": label, "qname": None,
                            "watch": None, "kind": "new_device"})
        log.info("ALERT  new device on the LAN: %s %s %s", ip, mac or "", name or "")

    # -- red light ---------------------------------------------------------
    def _pick_board(self) -> str | None:
        if self.s.alarm_device:
            return self.s.alarm_device
        try:
            for did, d in self.registry.devices.items():
                if did.startswith("esp32-") and self.registry._health(d) in ("online", "alerting"):
                    return did
        except Exception:
            pass
        return None

    async def _set_light(self, on: bool) -> None:
        board = self.light_device if (not on and self.light_device) else self._pick_board()
        self.light_on = on
        if not board:
            self.light_error = "no online ESP32 to show the light"
            return
        self.light_device = board
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "set_actuator",
                          "arguments": {"channel": self.s.alarm_channel, "value": 1 if on else 0}}}
        try:
            r = await self.registry.call_device(board, rpc, self.s.device_rpc_timeout_s)
            self.light_error = None if r.get("ok") else str(r.get("error") or r.get("response"))
            log.info("Alarm light %s on %s/%s -> %s", "ON" if on else "off", board,
                     self.s.alarm_channel, "ok" if r.get("ok") else self.light_error)
        except Exception as e:  # noqa: BLE001
            self.light_error = str(e)

    # -- housekeeping ------------------------------------------------------
    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(5)
                now = time.time()
                for (ip, w), last in list(self.active.items()):
                    if now - last > self.s.watch_hold_s:
                        self.active.pop((ip, w), None)
                        await self.store.clear_alert(f"lan-{ip}", f"watch:{w}", now)
                for ip, at in list(self.new_devices.items()):
                    if now - at > self.s.new_device_hold_s:
                        self.new_devices.pop(ip, None)
                        await self.store.clear_alert(f"lan-{ip}", "lan:new", now)
                if self.light_on and now - self.last_hit > self.s.alarm_light_hold_s:
                    await self._set_light(False)
                if int(now) % 60 < 5:
                    self.labels.clear()               # pick up renamed devices
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("network watch loop error")

    def stats(self) -> dict[str, Any]:
        return {
            "watching": self.watch,
            "hold_s": self.s.watch_hold_s,
            "active": [{"ip": ip, "watch": w, "last_hit": t} for (ip, w), t in self.active.items()],
            "light": {"on": self.light_on, "device": self.light_device or self._pick_board(),
                      "channel": self.s.alarm_channel, "error": self.light_error},
            "recent_events": list(self.events)[-50:][::-1],
        }
