import time
import math
import random
import asyncio
import logging
from typing import Dict, Any, List, Optional
import aiohttp

logger = logging.getLogger("sentinel.mikrotik")

class MikroTikCollector:
    """
    Collector service that pulls live interface statistics, DHCP leases,
    and firewall logs from MikroTik CRS326-24G-2S+ (Kelvin Drive).
    Includes realistic synthetic simulation for offline demo/development.
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.site_config = config.get("site", {})
        self.net_config = config.get("network", {})
        self.mikrotik_cfg = config.get("mikrotik_connection", {})
        
        self.host = self.mikrotik_cfg.get("api_host", "10.53.20.1")
        self.port = self.mikrotik_cfg.get("api_port", 8728)
        self.user = self.mikrotik_cfg.get("api_user", "sentinel_readonly")
        self.password = self.mikrotik_cfg.get("api_password", "")
        self.simulate_if_offline = self.mikrotik_cfg.get("simulate_if_offline", True)

        self.last_poll_time = time.time()
        self.is_connected = False
        self._step_counter = 0

    async def fetch_interface_stats(self) -> Dict[str, Any]:
        """
        Queries MikroTik RouterOS REST API for interface byte counters.
        Falls back to realistic simulation if router is offline.
        """
        try:
            headers = {"Authorization": aiohttp.encode_basic_auth(self.user, self.password)}
            url = f"https://{self.host}/rest/interface"
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, ssl=False, timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.is_connected = True
                        return {"status": "LIVE", "data": data}
        except Exception as e:
            logger.debug(f"MikroTik direct API unreachable ({e}). Using simulated live telemetry.")
            self.is_connected = False

        if self.simulate_if_offline:
            return self._generate_synthetic_telemetry()
        
        return {"status": "OFFLINE", "data": []}

    def _generate_synthetic_telemetry(self) -> Dict[str, Any]:
        """
        Generates realistic dynamic traffic patterns matching the Kelvin Drive topology:
        - WAN1 (vlan 11 Liquid Fibre): Primary uplink, ~60-150 Mbps with diurnal variation
        - WAN2 (ether9 Liquid Fibre): Secondary uplink, ~20-50 Mbps
        - LAN Bonding (ether2-5): Aggregated corporate traffic
        - PBX (ether10): VoIP SIP/RTP streams (169.239.8.0/24) ~2-8 Mbps low jitter
        - Floors (GF, FF, LG) with typical office distributions
        """
        self._step_counter += 1
        t = time.time()
        
        # Diurnal curve + random burstiness
        base_wave = (math.sin(t / 60.0) + 1.0) / 2.0 # 0.0 to 1.0
        burst = random.uniform(0.85, 1.15)
        
        # WAN 1 Primary (vlan 11 Liquid Fibre 102.33.102.218/29)
        wan1_rx = round(max(5.0, (75.0 + 65.0 * base_wave) * burst), 2)
        wan1_tx = round(max(2.0, (35.0 + 30.0 * base_wave) * burst), 2)
        
        # WAN 2 Secondary (ether9 Liquid Fibre 41.169.150.194/29)
        wan2_rx = round(max(2.0, (20.0 + 15.0 * (1.0 - base_wave)) * burst), 2)
        wan2_tx = round(max(1.0, (10.0 + 8.0 * (1.0 - base_wave)) * burst), 2)

        # Floor distributions
        total_rx = wan1_rx + wan2_rx
        total_tx = wan1_tx + wan2_tx

        # Ground Floor (Offices 1-19, Co-Working vlan 610): ~40%
        gf_rx = round(total_rx * 0.40 * random.uniform(0.9, 1.1), 2)
        gf_tx = round(total_tx * 0.38 * random.uniform(0.9, 1.1), 2)
        gf_devices = int(32 + 8 * base_wave + random.randint(-2, 3))

        # First Floor (Offices 1-27, Office 22, Printers vlan 400): ~45%
        ff_rx = round(total_rx * 0.45 * random.uniform(0.9, 1.1), 2)
        ff_tx = round(total_tx * 0.46 * random.uniform(0.9, 1.1), 2)
        ff_devices = int(48 + 12 * base_wave + random.randint(-3, 4))

        # Lower Ground (Offices 1-10, LG Printer): ~12%
        lg_rx = round(total_rx * 0.12 * random.uniform(0.9, 1.1), 2)
        lg_tx = round(total_tx * 0.13 * random.uniform(0.9, 1.1), 2)
        lg_devices = int(14 + 4 * base_wave + random.randint(-1, 2))

        # PBX VoIP (ether10)
        pbx_rx = round(random.uniform(2.5, 6.8), 2)
        pbx_tx = round(random.uniform(2.5, 6.8), 2)

        return {
            "status": "SIMULATED_LIVE",
            "timestamp": time.time(),
            "wan1": {
                "id": "wan1",
                "name": "Liquid Fibre ISP 1 (vlan 11)",
                "interface": "vlan11",
                "rx_mbps": wan1_rx,
                "tx_mbps": wan1_tx,
                "ip_pool": "102.33.102.218/29",
                "gateway_ip": "102.33.102.217",
                "status": "ONLINE"
            },
            "wan2": {
                "id": "wan2",
                "name": "Liquid Fibre ISP 2 (ether9)",
                "interface": "ether9",
                "rx_mbps": wan2_rx,
                "tx_mbps": wan2_tx,
                "ip_pool": "41.169.150.194/29",
                "gateway_ip": "41.169.150.193",
                "status": "ONLINE"
            },
            "floors": {
                "ground_floor": {"rx_mbps": gf_rx, "tx_mbps": gf_tx, "devices": gf_devices},
                "first_floor": {"rx_mbps": ff_rx, "tx_mbps": ff_tx, "devices": ff_devices},
                "lower_ground": {"rx_mbps": lg_rx, "tx_mbps": lg_tx, "devices": lg_devices},
                "pbx": {"rx_mbps": pbx_rx, "tx_mbps": pbx_tx, "devices": 18}
            },
            "total_rx_mbps": round(total_rx, 2),
            "total_tx_mbps": round(total_tx, 2)
        }
