"""
AEDI - IONITY GLOBAL | Network-wide alerts (watchlist hits, new devices) + the alarm light
Doc ID: DOC-2026-10-ESP32MCP-NETALERT | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Turns LAN activity into ordinary fleet alerts on the dashboard and in MCP:

  watch:<category>  a client hit an ALERT rule of the watchlist (app/rules). Fed by the
                    DNS pipeline - from ANY source: an ESP32 or Pi node's forwarder or
                    the host's own resolver. Clears after watch_hold_s without hits.
  lan:new           a device that was not on the network before has joined (host LAN sweep).

The red light: nodes running firmware 3.1+ / Pi agent 2 light their own alert
LED the moment they flag a query. The host additionally drives
IONITY_ALARM_DEVICE (any node, either protocol) - or, when none is set, the
first online fw 2.x ESP32 (v2.2 behaviour) - while any watch alert is active.
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
    def __init__(self, store, registry, settings, hub=None):
        self.store = store
        self.registry = registry
        self.s = settings
        self.hub = hub
        self.ignore = {x.strip() for x in settings.watch_ignore.split(",") if x.strip()}
        self.active: dict[tuple[str, str], dict[str, Any]] = {}     # (ip, category) -> last flag
        self.new_devices: dict[str, float] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=300)
        self.light_on = False
        self.light_device: str | None = None
        self.last_hit = 0.0
        self.light_error: str | None = None
        self._task: asyncio.Task | None = None
        self._busy: set[asyncio.Task] = set()

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        log.info("Network watch: hold %ss, alarm board %s", self.s.watch_hold_s,
                 self.s.alarm_device or "auto (first online fw 2.x ESP32)")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self.light_on:
            await self._set_light(False)
        for t in list(self._busy):
            t.cancel()

    # -- inputs ------------------------------------------------------------
    async def on_flag(self, ev: dict[str, Any], is_new: bool) -> None:
        """DnsPipeline flag listener (event loop)."""
        ip = ev["client_ip"]
        if ip in self.ignore:
            return
        key = (ip, ev["category"])
        fresh = key not in self.active
        self.active[key] = {**ev, "last_hit": time.time()}
        self.last_hit = time.time()
        if fresh:
            who = ev.get("label") or ev.get("hostname") or ip
            await self.store.raise_alert(Alert(
                device_id=f"lan-{ip}", severity="warning", code=f"watch:{ev['category']}",
                message=f"{who} ({ip}) opened {ev['qname']} [{ev['category']}]", raised_at=ev["ts"]))
            self.events.append({"ts": ev["ts"], "ip": ip, "label": who, "qname": ev["qname"],
                                "watch": ev["category"], "rule": ev["rule"], "node": ev.get("node_id"),
                                "kind": "watch"})
            log.info("ALERT  %s (%s) opened %s  [%s via %s]", who, ip, ev["qname"], ev["category"],
                     ev.get("node_id") or "host resolver")
        if not self.light_on:
            await self._set_light(True)

    def on_new_device(self, ip: str, mac: str | None, name: str | None) -> None:
        """Called by the host LAN sweep the first time an address shows up."""
        if ip in self.ignore:
            return
        t = asyncio.get_running_loop().create_task(self._new_device(ip, mac, name))
        self._busy.add(t)
        t.add_done_callback(self._busy.discard)

    async def _new_device(self, ip: str, mac: str | None, name: str | None) -> None:
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
        for did, d in self.registry.devices.items():
            if did.startswith("esp32-") and d.get("protocol") != "ewp1" \
                    and self.registry._health(d) in ("online", "alerting"):
                return did
        return None

    async def _set_light(self, on: bool) -> None:
        board = self.light_device if (not on and self.light_device) else self._pick_board()
        self.light_on = on
        if not board:
            self.light_error = None if on else self.light_error
            return
        self.light_device = board
        from app.fleet.actions import ActionError, node_action
        params: dict[str, Any] = {"on": on, "channel": self.s.alarm_channel}
        try:
            r = await node_action(board, "set_alert_led", params, registry=self.registry, hub=self.hub)
            self.light_error = None if r.get("ok") else str(r.get("error") or r.get("response"))
            log.info("Alarm light %s on %s -> %s", "ON" if on else "off", board,
                     "ok" if r.get("ok") else self.light_error)
        except (ActionError, Exception) as e:          # noqa: BLE001 - the light is best effort
            self.light_error = str(e)

    # -- housekeeping ------------------------------------------------------
    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(5)
                now = time.time()
                for (ip, cat), ev in list(self.active.items()):
                    if now - ev["last_hit"] > self.s.watch_hold_s:
                        self.active.pop((ip, cat), None)
                        await self.store.clear_alert(f"lan-{ip}", f"watch:{cat}", now)
                for ip, at in list(self.new_devices.items()):
                    if now - at > self.s.new_device_hold_s:
                        self.new_devices.pop(ip, None)
                        await self.store.clear_alert(f"lan-{ip}", "lan:new", now)
                if self.light_on and now - self.last_hit > self.s.alarm_light_hold_s:
                    await self._set_light(False)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("network watch loop error")

    def stats(self) -> dict[str, Any]:
        return {
            "hold_s": self.s.watch_hold_s,
            "active": [{"ip": ip, "category": cat, "label": ev.get("label"), "qname": ev.get("qname"),
                        "last_hit": ev["last_hit"], "node": ev.get("node_id")}
                       for (ip, cat), ev in self.active.items()],
            "light": {"on": self.light_on, "device": self.light_device or self._pick_board(),
                      "channel": self.s.alarm_channel, "error": self.light_error},
            "recent_events": list(self.events)[-50:][::-1],
        }
