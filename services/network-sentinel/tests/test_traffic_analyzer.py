import unittest
import asyncio
from core.mikrotik_collector import MikroTikCollector
from core.probe_engine import ProbeEngine
from core.loadshedding import LoadSheddingEngine
from core.speedtest_engine import SpeedtestEngine
from core.security_analyzer import SecurityAnalyzer
from core.traffic_analyzer import TrafficAnalyzer


async def fake_prober(ip, method, timeout):
    return None          # offline test: no network, a failed probe is a failure

class TestTrafficAnalyzer(unittest.TestCase):
    def setUp(self):
        self.config = {
            "site": {
                "id": "test-site",
                "name": "Test Kelvin Drive",
                "router_model": "MikroTik CRS326-24G-2S+",
                "hostname": "Dynamic@KelvinDrive"
            },
            "network": {},
            "sentinel": {
                "ping_targets": [{"name": "DNS", "ip": "8.8.8.8"}]
            },
            "loadshedding": {}
        }
        self.collector = MikroTikCollector(self.config)
        self.probe_engine = ProbeEngine(self.config, prober=fake_prober)
        self.ls_engine = LoadSheddingEngine(self.config)
        self.speedtest = SpeedtestEngine(self.config)
        self.sec = SecurityAnalyzer(self.config)
        self.analyzer = TrafficAnalyzer(
            config=self.config,
            collector=self.collector,
            probe_engine=self.probe_engine,
            loadshedding_engine=self.ls_engine,
            speedtest_engine=self.speedtest,
            security_analyzer=self.sec
        )

    def test_stability_score_calculation_optimal(self):
        result = self.analyzer.compute_stability_score(
            packet_loss_pct=0.0,
            jitter_ms=1.2,
            avg_latency_ms=10.0,
            wan1_online=True,
            wan2_online=True
        )
        self.assertEqual(result["score"], 100.0)
        self.assertEqual(result["status_label"], "EXCELLENT")

    def test_stability_score_calculation_degraded(self):
        result = self.analyzer.compute_stability_score(
            packet_loss_pct=5.0, # 25 pt penalty
            jitter_ms=5.0,       # 14 pt penalty
            avg_latency_ms=30.0, # 7.5 pt penalty
            wan1_online=True,
            wan2_online=True
        )
        self.assertLess(result["score"], 70.0)
        self.assertIn(result["status_label"], ["DEGRADED", "CRITICAL"])

    def test_stability_score_no_data(self):
        r = self.analyzer.compute_stability_score(None, None, None, None, None)
        self.assertIsNone(r["score"])
        self.assertEqual(r["status_label"], "NO_DATA")

    def test_stability_score_total_outage(self):
        result = self.analyzer.compute_stability_score(
            packet_loss_pct=100.0,
            jitter_ms=999.0,
            avg_latency_ms=999.0,
            wan1_online=False,
            wan2_online=False
        )
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["status_label"], "CRITICAL_OUTAGE")

    def test_live_telemetry_gathering(self):
        loop = asyncio.new_event_loop()
        telemetry = loop.run_until_complete(self.analyzer.get_live_telemetry())
        loop.close()

        self.assertIn("site_id", telemetry)
        self.assertIn("wan_interfaces", telemetry)
        self.assertIn("stability", telemetry)
        self.assertIn("floors", telemetry)
        # no router configured, nothing answered: unknown, never invented
        self.assertEqual(telemetry["wan_interfaces"], [])
        self.assertEqual(telemetry["floors"], [])
        self.assertIsNone(telemetry["total_rx_mbps"])
        # every probe failed: that IS a measurement (100 % loss), latency stays unknown
        self.assertEqual(telemetry["stability"]["packet_loss_avg_pct"], 100.0)
        self.assertIsNone(telemetry["stability"]["latency_avg_ms"])
        self.assertEqual(telemetry["stability"]["status_label"], "CRITICAL")
        self.assertEqual(telemetry["data_sources"]["router"]["status"], "UNCONFIGURED")
        self.assertEqual(telemetry["recent_threats"], [])
        self.assertIsNone(telemetry["hardware_sentinel"]["mains_power_ok"])

if __name__ == "__main__":
    unittest.main()
