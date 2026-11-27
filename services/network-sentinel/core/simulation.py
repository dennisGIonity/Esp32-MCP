"""
AEDI - IONITY GLOBAL | Network sentinel | core/simulation.py   Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

SIMULATED DEMO DATA - the only place in the sentinel that makes numbers up.

Used only when an operator explicitly sets a simulate_* flag in config.yaml
(all default to false). Every value carries simulated: true and
source: "simulation", and the dashboard shows a SIMULATED badge, so nobody can
mistake it for a measurement.
"""
from __future__ import annotations

import math
import random
import time
from typing import Any


def synthetic_interface_stats(wans: dict[str, dict[str, Any]], floor_names: dict[str, str], why: str) -> dict[str, Any]:
    t = time.time()
    wave = (math.sin(t / 60.0) + 1.0) / 2.0
    out_wans = {}
    for i, (wid, w) in enumerate(wans.items()):
        scale = 1.0 if i == 0 else 0.35
        out_wans[wid] = {"id": wid, "name": w.get("name", wid), "interface": w.get("interface"),
                         "ip_pool": w.get("ip_pool"), "gateway_ip": w.get("gateway_ip"), "status": "ONLINE",
                         "rx_mbps": round((40 + 80 * wave) * scale * random.uniform(0.9, 1.1), 2),
                         "tx_mbps": round((15 + 30 * wave) * scale * random.uniform(0.9, 1.1), 2)}
    rx = sum(w["rx_mbps"] for w in out_wans.values())
    tx = sum(w["tx_mbps"] for w in out_wans.values())
    floors = {fid: {"name": name, "interfaces": [], "missing_interfaces": [], "devices": None,
                    "rx_mbps": round(rx / max(1, len(floor_names)), 2),
                    "tx_mbps": round(tx / max(1, len(floor_names)), 2)} for fid, name in floor_names.items()}
    return {"status": "SIMULATED", "source": "simulation", "simulated": True, "error": why, "timestamp": t,
            "interfaces": {}, "wan1": out_wans.get("wan1", {}), "wan2": out_wans.get("wan2", {}), "wans": out_wans,
            "floors": floors, "unmapped": [], "total_rx_mbps": round(rx, 2), "total_tx_mbps": round(tx, 2),
            "router": None, "dhcp_leases": None}


def synthetic_speedtest(note: str) -> dict[str, Any]:
    from datetime import datetime, timezone
    dl = round(random.uniform(175.0, 198.5), 1)
    return {"source": "simulation", "simulated": True, "timestamp": datetime.now(timezone.utc).isoformat(),
            "download_mbps": dl, "upload_mbps": round(random.uniform(168.0, 195.0), 1),
            "ping_ms": round(random.uniform(5.5, 9.8), 1), "jitter_ms": round(random.uniform(0.6, 2.1), 1),
            "server_location": None, "isp_detected": None, "speed_bar_level": None, "note": note}
