import asyncio
import logging
import random
import time
from typing import Any, Dict

logger = logging.getLogger("sentinel.speedtest")

try:
    import speedtest  # speedtest-cli (requirements.txt)
except Exception:  # noqa: BLE001 - optional at runtime
    speedtest = None


class SpeedtestEngine:
    """
    Speedtest engine:
      * active test via speedtest-cli, run off the event loop
      * optional, clearly labelled simulation when the CLI is unavailable
      * passive continuous link-capacity tracking from live wire counters
    Every result carries `source` and `simulated` so no consumer (dashboard,
    REST, MCP agent) can mistake synthetic numbers for a real ISP test.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        st_cfg = config.get("speedtest", {}) or {}
        self.committed_dl = float(st_cfg.get("committed_download_mbps", 200.0))
        self.committed_ul = float(st_cfg.get("committed_upload_mbps", 200.0))
        self.allow_simulation = bool(st_cfg.get("simulate_if_unavailable", False))
        self.last_result: Dict[str, Any] = {
            "source": "none", "simulated": True,
            "download_mbps": None, "upload_mbps": None, "ping_ms": None, "jitter_ms": None,
            "server_location": None, "isp_detected": None, "speed_bar_level": 0,
            "timestamp": None, "note": "no speed test has run yet",
        }
        self._lock = asyncio.Lock()

    @property
    def is_running_test(self) -> bool:
        return self._lock.locked()

    def _real_test(self) -> Dict[str, Any]:
        s = speedtest.Speedtest(secure=True)
        s.get_best_server()
        dl = s.download() / 1e6
        ul = s.upload(pre_allocate=False) / 1e6
        r = s.results.dict()
        return {
            "source": "speedtest-cli", "simulated": False,
            "download_mbps": round(dl, 1), "upload_mbps": round(ul, 1),
            "ping_ms": round(float(r.get("ping", 0.0)), 1), "jitter_ms": None,
            "server_location": f"{r['server'].get('sponsor')} - {r['server'].get('name')}",
            "isp_detected": r.get("client", {}).get("isp"),
            "speed_bar_level": int(min(100, dl / self.committed_dl * 100)),
            "timestamp": time.time(),
        }

    def _simulated(self) -> Dict[str, Any]:
        dl = round(random.uniform(175.0, 198.5), 1)
        return {
            "source": "simulation", "simulated": True,
            "download_mbps": dl, "upload_mbps": round(random.uniform(168.0, 195.0), 1),
            "ping_ms": round(random.uniform(5.5, 9.8), 1), "jitter_ms": round(random.uniform(0.6, 2.1), 1),
            "server_location": "SIMULATED", "isp_detected": "SIMULATED",
            "speed_bar_level": int(min(100, dl / self.committed_dl * 100)),
            "timestamp": time.time(),
            "note": "speedtest-cli unavailable and speedtest.simulate_if_unavailable=true",
        }

    async def run_active_speedtest(self) -> Dict[str, Any]:
        if self._lock.locked():
            return {**self.last_result, "note": "a test is already running"}
        async with self._lock:
            if speedtest is not None:
                try:
                    # A real test is ~20-40 s of blocking I/O: keep it off the event loop.
                    self.last_result = await asyncio.wait_for(asyncio.to_thread(self._real_test), timeout=90)
                    logger.info("[Speedtest] %s/%s Mbps, %s ms", self.last_result["download_mbps"],
                                self.last_result["upload_mbps"], self.last_result["ping_ms"])
                    return self.last_result
                except Exception as e:  # noqa: BLE001
                    logger.warning("[Speedtest] real test failed: %s", e)
            if self.allow_simulation:
                await asyncio.sleep(3.0)
                self.last_result = self._simulated()
                return self.last_result
            self.last_result = {**self.last_result, "source": "none", "simulated": True,
                                "note": "speedtest-cli not installed or failed; simulation disabled"}
            return self.last_result

    def get_passive_estimate(self, current_rx_mbps: float, current_tx_mbps: float) -> Dict[str, Any]:
        """Continuous passive health metrics based on live wire consumption."""
        return {
            "current_rx_mbps": current_rx_mbps,
            "current_tx_mbps": current_tx_mbps,
            "committed_capacity_mbps": self.committed_dl + self.committed_ul,
            "rx_utilization_pct": round((current_rx_mbps / self.committed_dl) * 100.0, 1),
            "tx_utilization_pct": round((current_tx_mbps / self.committed_ul) * 100.0, 1),
        }
