import time
import math
import logging
from typing import Dict, Any, List
from datetime import datetime, timezone

logger = logging.getLogger("sentinel.traffic")

class TrafficAnalyzer:
    """
    Synthesizes interface statistics, active probes, security alerts, and power status
    into unified live network telemetry and stability index.
    """
    def __init__(self, config: Dict[str, Any], collector, probe_engine, loadshedding_engine, speedtest_engine, security_analyzer):
        self.config = config
        self.collector = collector
        self.probe_engine = probe_engine
        self.loadshedding_engine = loadshedding_engine
        self.speedtest_engine = speedtest_engine
        self.security_analyzer = security_analyzer
        
        self.site_id = config.get("site", {}).get("id", "kelvin-drive-site-01")
        self.site_name = config.get("site", {}).get("name", "Kelvin Drive HQ")
        self.router_model = config.get("site", {}).get("router_model", "MikroTik CRS326-24G-2S+")
        self.router_hostname = config.get("site", {}).get("hostname", "Dynamic@KelvinDrive")

        self.hardware_mains_ok = True
        self.last_hardware_ping_ts = time.time()

    def update_hardware_sentinel_state(self, mains_ok: bool):
        self.hardware_mains_ok = mains_ok
        self.last_hardware_ping_ts = time.time()

    def compute_stability_score(self, packet_loss_pct: float, jitter_ms: float, avg_latency_ms: float, wan1_online: bool, wan2_online: bool) -> Dict[str, Any]:
        """
        Computes weighted Stability Index (0 - 100%):
        - Packet Loss penalty (50% weight)
        - Jitter penalty (30% weight)
        - Latency penalty (20% weight)
        """
        if not wan1_online and not wan2_online:
            return {
                "score": 0.0,
                "status_label": "CRITICAL_OUTAGE",
                "loss_pct": 100.0,
                "jitter_ms": 999.0,
                "latency_ms": 999.0
            }

        # Base 100
        loss_penalty = min(50.0, packet_loss_pct * 5.0) # 10% loss = 50 pt penalty
        jitter_penalty = min(30.0, max(0.0, (jitter_ms - 1.5) * 4.0)) # >1.5ms jitter incurs penalty
        latency_penalty = min(20.0, max(0.0, (avg_latency_ms - 15.0) * 0.5))

        score = max(0.0, 100.0 - (loss_penalty + jitter_penalty + latency_penalty))
        score = round(score, 1)

        if score >= 95.0:
            status_label = "EXCELLENT"
        elif score >= 85.0:
            status_label = "STABLE"
        elif score >= 70.0:
            status_label = "DEGRADED"
        else:
            status_label = "CRITICAL"

        return {
            "score": score,
            "status_label": status_label,
            "loss_pct": packet_loss_pct,
            "jitter_ms": jitter_ms,
            "latency_ms": avg_latency_ms
        }

    async def get_live_telemetry(self) -> Dict[str, Any]:
        """
        Gathers real-time telemetry from all sub-engines.
        """
        # 1. Interface stats from MikroTik
        interface_data = await self.collector.fetch_interface_stats()
        
        # 2. Probes
        probe_data = await self.probe_engine.run_all_probes()
        
        # 3. Load Shedding & Grid Correlation
        ls_status = await self.loadshedding_engine.get_loadshedding_status()
        
        # 4. Security threats
        self.security_analyzer.simulate_random_threat()
        recent_threats = self.security_analyzer.get_recent_alerts(limit=5)
        
        # 5. Extract rates
        wan1_data = interface_data.get("wan1", {})
        wan2_data = interface_data.get("wan2", {})
        floors_raw = interface_data.get("floors", {})
        total_rx = interface_data.get("total_rx_mbps", 0.0)
        total_tx = interface_data.get("total_tx_mbps", 0.0)

        # 6. Stability calculation
        loss_pct = probe_data.get("overall_loss_pct", 0.0)
        jitter_ms = probe_data.get("overall_jitter_ms", 1.5)
        latency_ms = probe_data.get("overall_latency_ms", 10.0)
        wan1_online = wan1_data.get("status") == "ONLINE"
        wan2_online = wan2_data.get("status") == "ONLINE"

        stability = self.compute_stability_score(loss_pct, jitter_ms, latency_ms, wan1_online, wan2_online)

        # 7. Correlation analysis
        correlation = self.loadshedding_engine.correlate_link_issue(
            wan_status="ONLINE" if (wan1_online or wan2_online) else "OFFLINE",
            packet_loss_pct=loss_pct,
            mains_power_ok=self.hardware_mains_ok
        )

        # 8. Floor Breakdown
        floors = [
            {
                "floor_id": "ground_floor",
                "floor_name": "Ground Floor (Offices 1-19, Co-Working)",
                "active_devices": floors_raw.get("ground_floor", {}).get("devices", 38),
                "rx_mbps": floors_raw.get("ground_floor", {}).get("rx_mbps", 0.0),
                "tx_mbps": floors_raw.get("ground_floor", {}).get("tx_mbps", 0.0),
                "bandwidth_share_pct": round((floors_raw.get("ground_floor", {}).get("rx_mbps", 0.0) / max(0.1, total_rx)) * 100, 1),
                "primary_vlans": ["10.53.61.x - 10.53.79.x", "VLAN 610"]
            },
            {
                "floor_id": "first_floor",
                "floor_name": "First Floor (Offices 1-27, Office 22, Printers)",
                "active_devices": floors_raw.get("first_floor", {}).get("devices", 54),
                "rx_mbps": floors_raw.get("first_floor", {}).get("rx_mbps", 0.0),
                "tx_mbps": floors_raw.get("first_floor", {}).get("tx_mbps", 0.0),
                "bandwidth_share_pct": round((floors_raw.get("first_floor", {}).get("rx_mbps", 0.0) / max(0.1, total_rx)) * 100, 1),
                "primary_vlans": ["10.53.81.x - 10.53.107.x", "VLAN 400", "10.53.102.27"]
            },
            {
                "floor_id": "lower_ground",
                "floor_name": "Lower Ground (Offices 1-10, LG Printer)",
                "active_devices": floors_raw.get("lower_ground", {}).get("devices", 16),
                "rx_mbps": floors_raw.get("lower_ground", {}).get("rx_mbps", 0.0),
                "tx_mbps": floors_raw.get("lower_ground", {}).get("tx_mbps", 0.0),
                "bandwidth_share_pct": round((floors_raw.get("lower_ground", {}).get("rx_mbps", 0.0) / max(0.1, total_rx)) * 100, 1),
                "primary_vlans": ["10.53.111.x - 10.53.120.x", "10.53.120.251"]
            },
            {
                "floor_id": "pbx_voip",
                "floor_name": "PBX Telephony Uplink (UDP/TCP 169.239.8.0/24)",
                "active_devices": floors_raw.get("pbx", {}).get("devices", 18),
                "rx_mbps": floors_raw.get("pbx", {}).get("rx_mbps", 0.0),
                "tx_mbps": floors_raw.get("pbx", {}).get("tx_mbps", 0.0),
                "bandwidth_share_pct": round((floors_raw.get("pbx", {}).get("rx_mbps", 0.0) / max(0.1, total_rx)) * 100, 1),
                "primary_vlans": ["ether10 (PBX) -> 192.168.243.250"]
            }
        ]

        # 9. Assembled Master Telemetry
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "site_id": self.site_id,
            "site_name": self.site_name,
            "router_hostname": self.router_hostname,
            "router_model": self.router_model,
            "total_rx_mbps": total_rx,
            "total_tx_mbps": total_tx,
            "peak_throughput_capacity_mbps": 400.0,
            "wan_interfaces": [
                {
                    "id": "wan1",
                    "name": "Liquid Fibre ISP 1 (vlan 11)",
                    "interface": "vlan11",
                    "ip_pool": "102.33.102.218/29",
                    "gateway_ip": "102.33.102.217",
                    "status": "ONLINE" if wan1_online else "OFFLINE",
                    "rx_mbps": wan1_data.get("rx_mbps", 0.0),
                    "tx_mbps": wan1_data.get("tx_mbps", 0.0),
                    "latency_ms": latency_ms,
                    "packet_loss_pct": loss_pct,
                    "jitter_ms": jitter_ms,
                    "priority": 1,
                    "committed_bandwidth_mbps": 200.0
                },
                {
                    "id": "wan2",
                    "name": "Liquid Fibre ISP 2 (ether9)",
                    "interface": "ether9",
                    "ip_pool": "41.169.150.194/29",
                    "gateway_ip": "41.169.150.193",
                    "status": "ONLINE" if wan2_online else "OFFLINE",
                    "rx_mbps": wan2_data.get("rx_mbps", 0.0),
                    "tx_mbps": wan2_data.get("tx_mbps", 0.0),
                    "latency_ms": round(latency_ms + 1.2, 1),
                    "packet_loss_pct": loss_pct,
                    "jitter_ms": round(jitter_ms + 0.4, 1),
                    "priority": 2,
                    "committed_bandwidth_mbps": 200.0
                }
            ],
            "floors": floors,
            "stability": {
                "stability_score": stability["score"],
                "status_label": stability["status_label"],
                "packet_loss_avg_pct": loss_pct,
                "jitter_avg_ms": jitter_ms,
                "latency_avg_ms": latency_ms,
                "uptime_seconds": 864000,
                "failover_active": not wan1_online and wan2_online,
                "active_wan_id": "wan1" if wan1_online else "wan2"
            },
            "loadshedding": {
                "area_id": ls_status.get("area_id"),
                "area_name": ls_status.get("area_name"),
                "current_stage": ls_status.get("current_stage", 0),
                "is_currently_loadshedding": ls_status.get("is_currently_loadshedding", False),
                "next_event": ls_status.get("next_event"),
                "power_grid_health": ls_status.get("power_grid_health"),
                "correlation_analysis": correlation
            },
            "speedtest": self.speedtest_engine.last_result,
            "recent_threats": recent_threats,
            "probe_targets": probe_data.get("targets", []),
            "hardware_sentinel": {
                "connected": (time.time() - self.last_hardware_ping_ts) < 15.0,
                "mains_power_ok": self.hardware_mains_ok,
                "placement": "ALONGSIDE_ROUTER (Switch Mirror / Mgmt Port VLAN 200)"
            }
        }
