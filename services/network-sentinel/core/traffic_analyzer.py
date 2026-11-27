"""
AEDI - IONITY GLOBAL | Network sentinel | core/traffic_analyzer.py   Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Joins the router counters, the probes, the firewall log, load-shedding and the
speed test into one live telemetry document. Every number comes from one of
those sources; when a source is missing the value is null and `data_sources`
says why - the dashboard and the MCP agent can tell "0 Mbps" from "unknown".
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("sentinel.traffic")


class TrafficAnalyzer:
    def __init__(self, config: dict[str, Any], collector, probe_engine, loadshedding_engine, speedtest_engine,
                 security_analyzer):
        self.config = config
        self.collector = collector
        self.probe_engine = probe_engine
        self.loadshedding_engine = loadshedding_engine
        self.speedtest_engine = speedtest_engine
        self.security_analyzer = security_analyzer
        site = config.get("site", {})
        self.site_id = site.get("id", "site-01")
        self.site_name = site.get("name", "site")
        self.router_model = site.get("router_model")
        self.router_hostname = site.get("hostname")
        self.wan_cfg = {w["id"]: w for w in config.get("network", {}).get("wan_interfaces", [])}
        self.floor_cfg = {f["id"]: f for f in config.get("network", {}).get("floors", [])}
        self.log_poll_s = float(config.get("security", {}).get("log_poll_seconds", 10))
        self._last_log_poll = 0.0
        self.started_at = time.time()
        # Hardware sentinel (ESP32) feed: unknown until the first report arrives.
        self.hardware_mains_ok: bool | None = None
        self.last_hardware_ping_ts: float | None = None

    def update_hardware_sentinel_state(self, mains_ok: bool):
        self.hardware_mains_ok = mains_ok
        self.last_hardware_ping_ts = time.time()

    @staticmethod
    def compute_stability_score(packet_loss_pct: float | None, jitter_ms: float | None, avg_latency_ms: float | None,
                                wan1_online: bool | None, wan2_online: bool | None) -> dict[str, Any]:
        """
        Stability Index 0-100 from MEASURED probes: loss (50 % weight), jitter (30 %), latency (20 %).
        Both WANs known-down = 0. No probe data = score null, NO_DATA.
        """
        if wan1_online is False and wan2_online is False:
            return {"score": 0.0, "status_label": "CRITICAL_OUTAGE", "loss_pct": packet_loss_pct,
                    "jitter_ms": jitter_ms, "latency_ms": avg_latency_ms}
        if packet_loss_pct is None and avg_latency_ms is None:
            return {"score": None, "status_label": "NO_DATA", "loss_pct": None, "jitter_ms": None,
                    "latency_ms": None}
        loss_penalty = min(50.0, (packet_loss_pct or 0.0) * 5.0)
        jitter_penalty = min(30.0, max(0.0, ((jitter_ms or 0.0) - 1.5) * 4.0))
        latency_penalty = 20.0 if avg_latency_ms is None else min(20.0, max(0.0, (avg_latency_ms - 15.0) * 0.5))
        score = round(max(0.0, 100.0 - (loss_penalty + jitter_penalty + latency_penalty)), 1)
        label = ("EXCELLENT" if score >= 95.0 else "STABLE" if score >= 85.0 else
                 "DEGRADED" if score >= 70.0 else "CRITICAL")
        return {"score": score, "status_label": label, "loss_pct": packet_loss_pct, "jitter_ms": jitter_ms,
                "latency_ms": avg_latency_ms}

    async def _poll_firewall_log(self) -> None:
        now = time.monotonic()
        if now - self._last_log_poll < self.log_poll_s:
            return
        self._last_log_poll = now
        rows = await self.collector.fetch_firewall_log()
        if rows:
            self.security_analyzer.ingest_firewall_log(rows)

    @staticmethod
    def _online(w: dict[str, Any]) -> bool | None:
        st = w.get("status")
        return True if st == "ONLINE" else False if st == "OFFLINE" else None

    async def get_live_telemetry(self) -> dict[str, Any]:
        iface = await self.collector.fetch_interface_stats()
        probes = await self.probe_engine.run_all_probes()
        ls = await self.loadshedding_engine.get_loadshedding_status()
        await self._poll_firewall_log()

        wans = iface.get("wans") or {}
        total_rx, total_tx = iface.get("total_rx_mbps"), iface.get("total_tx_mbps")
        loss, jitter, latency = (probes.get("overall_loss_pct"), probes.get("overall_jitter_ms"),
                                 probes.get("overall_latency_ms"))
        w1, w2 = self._online(wans.get("wan1", {})), self._online(wans.get("wan2", {}))
        stability = self.compute_stability_score(loss, jitter, latency, w1, w2)
        wan_state = ("UNKNOWN" if w1 is None and w2 is None else "ONLINE" if (w1 or w2) else "OFFLINE")
        correlation = self.loadshedding_engine.correlate_link_issue(
            wan_status=wan_state, packet_loss_pct=loss,
            mains_power_ok=self.hardware_mains_ok is not False)

        wan_rows = []
        for wid, cfg in self.wan_cfg.items():
            live = wans.get(wid, {})
            gw = self.probe_engine.target_for(cfg.get("gateway_ip", "")) if cfg.get("gateway_ip") else None
            wan_rows.append({
                "id": wid, "name": cfg.get("name", wid), "interface": cfg.get("interface"),
                "ip_pool": cfg.get("ip_pool"), "gateway_ip": cfg.get("gateway_ip"),
                "status": live.get("status", "UNKNOWN"),
                "rx_mbps": live.get("rx_mbps"), "tx_mbps": live.get("tx_mbps"),
                # per-WAN quality = probes to that WAN's own gateway (null if it is not a probe target)
                "latency_ms": gw["avg_rtt_ms"] if gw else None,
                "packet_loss_pct": gw["packet_loss_pct"] if gw else None,
                "jitter_ms": gw["jitter_ms"] if gw else None,
                "priority": cfg.get("priority"), "committed_bandwidth_mbps": cfg.get("committed_bandwidth_mbps"),
            })

        floors = []
        for fid, f in (iface.get("floors") or {}).items():
            rx = f.get("rx_mbps")
            floors.append({
                "floor_id": fid, "floor_name": f.get("name", fid), "interfaces": f.get("interfaces", []),
                "missing_interfaces": f.get("missing_interfaces", []),
                "active_devices": f.get("devices"), "rx_mbps": rx, "tx_mbps": f.get("tx_mbps"),
                "bandwidth_share_pct": round(rx / total_rx * 100, 1) if rx is not None and total_rx else None,
                "primary_vlans": [v.get("name") for v in self.floor_cfg.get(fid, {}).get("vlans", [])],
            })

        router = iface.get("router") or {}
        hw_age = time.time() - self.last_hardware_ping_ts if self.last_hardware_ping_ts else None
        capacity = sum(float(w.get("committed_bandwidth_mbps") or 0) for w in self.wan_cfg.values()) or None
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "site_id": self.site_id, "site_name": self.site_name,
            "router_hostname": self.router_hostname, "router_model": router.get("board") or self.router_model,
            "total_rx_mbps": total_rx, "total_tx_mbps": total_tx,
            "peak_throughput_capacity_mbps": capacity,
            "wan_interfaces": wan_rows,
            "floors": floors,
            "unmapped_interfaces": iface.get("unmapped", []),
            "stability": {
                "stability_score": stability["score"], "status_label": stability["status_label"],
                "packet_loss_avg_pct": loss, "jitter_avg_ms": jitter, "latency_avg_ms": latency,
                "router_uptime_seconds": router.get("uptime_s"),
                "sentinel_uptime_seconds": int(time.time() - self.started_at),
                "failover_active": (w1 is False and w2 is True),
                "active_wan_id": "wan1" if w1 else "wan2" if w2 else None,
            },
            "loadshedding": {
                "available": ls.get("available", False), "reason": ls.get("reason"),
                "area_id": ls.get("area_id"), "area_name": ls.get("area_name"),
                "current_stage": ls.get("current_stage"),
                "is_currently_loadshedding": ls.get("is_currently_loadshedding"),
                "next_event": ls.get("next_event"), "power_grid_health": ls.get("power_grid_health"),
                "correlation_analysis": correlation,
            },
            "speedtest": self.speedtest_engine.last_result,
            "recent_threats": self.security_analyzer.get_recent_alerts(limit=5),
            "probe_targets": probes.get("targets", []),
            "hardware_sentinel": {
                "connected": hw_age is not None and hw_age < 15.0,
                "mains_power_ok": self.hardware_mains_ok,
                "last_report_age_s": round(hw_age, 1) if hw_age is not None else None,
            },
            "data_sources": {
                "router": {"status": iface.get("status"), "simulated": bool(iface.get("simulated")),
                           "error": iface.get("error")},
                "probes": {"probed": probes.get("probed"), "answered": probes.get("answered")},
                "loadshedding": {"available": ls.get("available", False), "source": ls.get("source")},
                "firewall_log": self.security_analyzer.stats(),
                "speedtest": {"source": (self.speedtest_engine.last_result or {}).get("source"),
                              "simulated": bool((self.speedtest_engine.last_result or {}).get("simulated"))},
            },
        }
