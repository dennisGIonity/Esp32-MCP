"""
AEDI - IONITY GLOBAL | Network sentinel | core/mikrotik_collector.py   Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Live data from the site router through the RouterOS v7 REST API
(https://<router>/rest/..., read-only user). Nothing here is invented:

  /rest/interface                rx-byte / tx-byte counters -> Mbps from the delta between
                                 two polls (null on the first poll and after a counter reset)
  /rest/ip/dhcp-server/lease     bound leases -> devices per floor (by the floor's subnets)
  /rest/log                      firewall topics -> SecurityAnalyzer (needs log=yes on the rules)
  /rest/system/resource          router uptime, board name, RouterOS version

Which interface belongs to which floor is CONFIGURATION, never a guess
(config.yaml `floor_interfaces`). Interfaces nobody mapped are reported as
`unmapped`, so a missing mapping is visible instead of silently wrong.

Status: LIVE (router answered) | OFFLINE (configured, not reachable) |
UNCONFIGURED (no password / host). Simulation exists only in
core/simulation.py, only with mikrotik_connection.simulate_if_offline: true,
and every value it produces is labelled simulated.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable

import aiohttp

logger = logging.getLogger("sentinel.mikrotik")

Fetch = Callable[[str], Awaitable[Any]]
_PLACEHOLDER = re.compile(r"^(|change_me.*|your_.*)$", re.I)


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_uptime(s: str | None) -> int | None:
    """RouterOS duration '1w2d3h4m5s' -> seconds."""
    if not s:
        return None
    total, found = 0, False
    for n, unit in re.findall(r"(\d+)([wdhms])", s):
        total += int(n) * {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        found = True
    return total if found else None


def _networks(spec: list[str]) -> list[ipaddress.IPv4Network]:
    out = []
    for s in spec or []:
        try:
            out.append(ipaddress.ip_network(s, strict=False))
        except ValueError:
            logger.warning("floor_interfaces: '%s' is not a CIDR - ignored", s)
    return out


class MikroTikCollector:
    def __init__(self, config: dict[str, Any], fetch: Fetch | None = None,
                 clock: Callable[[], float] = time.monotonic):
        m = config.get("mikrotik_connection", {})
        self.host = os.getenv("MIKROTIK_HOST") or m.get("api_host", "")
        self.scheme = m.get("rest_scheme", "https")
        self.port = int(m.get("rest_port", 443 if self.scheme == "https" else 80))
        self.user = os.getenv("MIKROTIK_USER") or m.get("api_user", "")
        self.password = os.getenv("MIKROTIK_PASSWORD") or m.get("api_password", "")
        self.verify_tls = bool(m.get("verify_tls", False))   # RouterOS ships a self-signed certificate
        self.timeout_s = float(m.get("timeout_seconds", 3.0))
        self.simulate_if_offline = bool(m.get("simulate_if_offline", False))
        net = config.get("network", {})
        self.wans = {w["id"]: w for w in net.get("wan_interfaces", []) if w.get("interface")}
        self.floor_names = {f["id"]: f.get("name", f["id"]) for f in net.get("floors", [])}
        self.floor_map: dict[str, dict[str, Any]] = {}
        for fid, spec in (config.get("floor_interfaces") or {}).items():
            spec = spec if isinstance(spec, dict) else {"interfaces": spec}
            self.floor_map[fid] = {"interfaces": list(spec.get("interfaces") or []),
                                   "subnets": _networks(spec.get("subnets") or [])}
        self._fetch = fetch or self._http_get
        self._clock = clock
        self._prev: dict[str, tuple[float, int, int]] = {}
        self.is_connected = False
        self.last_error: str | None = None
        self._sim = None

    @property
    def configured(self) -> bool:
        return bool(self.host) and bool(self.user) and not _PLACEHOLDER.match(self.password or "")

    async def _http_get(self, path: str) -> Any:
        url = f"{self.scheme}://{self.host}:{self.port}/rest/{path}"
        auth = aiohttp.BasicAuth(self.user, self.password)
        async with aiohttp.ClientSession(auth=auth) as s:
            async with s.get(url, ssl=None if self.verify_tls else False,
                             timeout=aiohttp.ClientTimeout(total=self.timeout_s)) as r:
                if r.status != 200:
                    raise RuntimeError(f"{path}: HTTP {r.status}")
                return await r.json()

    # -- interfaces ------------------------------------------------------------------
    def _rates(self, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        now = self._clock()
        out = {}
        for row in rows:
            name = row.get("name")
            rx, tx = _int(row.get("rx-byte")), _int(row.get("tx-byte"))
            if not name or rx is None or tx is None:
                continue
            prev = self._prev.get(name)
            self._prev[name] = (now, rx, tx)
            rx_mbps = tx_mbps = None
            if prev and now > prev[0] and rx >= prev[1] and tx >= prev[2]:
                dt = now - prev[0]
                rx_mbps = round((rx - prev[1]) * 8 / dt / 1e6, 3)
                tx_mbps = round((tx - prev[2]) * 8 / dt / 1e6, 3)
            out[name] = {"rx_mbps": rx_mbps, "tx_mbps": tx_mbps,
                         "running": str(row.get("running", "")).lower() == "true",
                         "disabled": str(row.get("disabled", "")).lower() == "true",
                         "type": row.get("type")}
        return out

    @staticmethod
    def _sum(ifaces: dict[str, dict[str, Any]], names: list[str], key: str) -> float | None:
        vals = [ifaces[n][key] for n in names if n in ifaces and ifaces[n][key] is not None]
        return round(sum(vals), 3) if vals else None

    def _leases_by_floor(self, leases: list[dict[str, Any]] | None) -> dict[str, int | None]:
        out: dict[str, int | None] = {}
        for fid, spec in self.floor_map.items():
            if leases is None or not spec["subnets"]:
                out[fid] = None
                continue
            n = 0
            for lease in leases:
                if lease.get("status") != "bound":
                    continue
                try:
                    ip = ipaddress.ip_address(lease.get("address", ""))
                except ValueError:
                    continue
                if any(ip in net for net in spec["subnets"]):
                    n += 1
            out[fid] = n
        return out

    async def fetch_interface_stats(self) -> dict[str, Any]:
        if not self.configured:
            self.is_connected = False
            return self._unavailable("UNCONFIGURED",
                                     "set MIKROTIK_HOST / MIKROTIK_USER / MIKROTIK_PASSWORD (read-only REST user)")
        try:
            rows = await self._fetch("interface")
        except Exception as e:                     # noqa: BLE001 - any transport problem = offline
            self.is_connected = False
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            logger.info("RouterOS REST unreachable: %s", self.last_error)
            return self._unavailable("OFFLINE", self.last_error)
        self.is_connected = True
        self.last_error = None
        try:
            leases = await self._fetch("ip/dhcp-server/lease")
        except Exception:                          # noqa: BLE001 - optional
            leases = None
        try:
            res = await self._fetch("system/resource")
        except Exception:                          # noqa: BLE001 - optional
            res = None
        ifaces = self._rates(rows if isinstance(rows, list) else [])
        devices = self._leases_by_floor(leases if isinstance(leases, list) else None)

        wans = {}
        for wid, w in self.wans.items():
            i = ifaces.get(w["interface"])
            wans[wid] = {"id": wid, "name": w.get("name", wid), "interface": w["interface"],
                         "ip_pool": w.get("ip_pool"), "gateway_ip": w.get("gateway_ip"),
                         "rx_mbps": i["rx_mbps"] if i else None, "tx_mbps": i["tx_mbps"] if i else None,
                         "status": ("UNKNOWN" if i is None else "ONLINE" if i["running"] and not i["disabled"]
                                    else "OFFLINE")}
        floors = {}
        mapped = {w["interface"] for w in self.wans.values()}
        for fid, spec in self.floor_map.items():
            names = spec["interfaces"]
            mapped.update(names)
            floors[fid] = {"name": self.floor_names.get(fid, fid), "interfaces": names,
                           "rx_mbps": self._sum(ifaces, names, "rx_mbps"),
                           "tx_mbps": self._sum(ifaces, names, "tx_mbps"),
                           "devices": devices.get(fid),
                           "missing_interfaces": [n for n in names if n not in ifaces]}
        wan_names = [w["interface"] for w in self.wans.values()]
        router = None
        if isinstance(res, dict):
            router = {"uptime_s": parse_uptime(res.get("uptime")), "board": res.get("board-name"),
                      "version": res.get("version"), "cpu_load_pct": _int(res.get("cpu-load"))}
        return {
            "status": "LIVE", "source": "routeros-rest", "simulated": False, "timestamp": time.time(),
            "interfaces": ifaces,
            "wan1": wans.get("wan1", {}), "wan2": wans.get("wan2", {}), "wans": wans,
            "floors": floors,
            "unmapped": sorted(n for n, i in ifaces.items() if n not in mapped and not i["disabled"]),
            "total_rx_mbps": self._sum(ifaces, wan_names, "rx_mbps"),
            "total_tx_mbps": self._sum(ifaces, wan_names, "tx_mbps"),
            "router": router,
            "dhcp_leases": len([x for x in leases if x.get("status") == "bound"]) if isinstance(leases, list) else None,
        }

    def _unavailable(self, status: str, why: str) -> dict[str, Any]:
        if self.simulate_if_offline:
            from core.simulation import synthetic_interface_stats   # clearly labelled demo data
            return synthetic_interface_stats(self.wans, self.floor_names, why)
        return {"status": status, "source": "routeros-rest", "simulated": False, "error": why,
                "timestamp": time.time(), "interfaces": {}, "wan1": {}, "wan2": {}, "wans": {},
                "floors": {}, "unmapped": [], "total_rx_mbps": None, "total_tx_mbps": None, "router": None,
                "dhcp_leases": None}

    async def fetch_firewall_log(self) -> list[dict[str, Any]]:
        """Firewall log lines (RouterOS /rest/log, topics containing 'firewall'). [] when unavailable."""
        if not self.configured or not self.is_connected:
            return []
        try:
            rows = await self._fetch("log")
        except Exception as e:                     # noqa: BLE001
            self.last_error = f"log: {type(e).__name__}: {e}"[:200]
            return []
        return [r for r in rows if isinstance(r, dict) and "firewall" in str(r.get("topics", ""))] \
            if isinstance(rows, list) else []
