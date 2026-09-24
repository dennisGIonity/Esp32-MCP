import unittest
import asyncio
from core.mikrotik_collector import MikroTikCollector
from core.probe_engine import ProbeEngine
from core.loadshedding import LoadSheddingEngine
from core.speedtest_engine import SpeedtestEngine
from core.security_analyzer import SecurityAnalyzer
from core.traffic_analyzer import TrafficAnalyzer

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
            "loadshedding": {"mock_if_no_key": True}
        }
        self.collector = MikroTikCollector(self.config)
        self.probe_engine = ProbeEngine(self.config)
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
        self.assertEqual(len(telemetry["wan_interfaces"]), 2)
        self.assertEqual(len(telemetry["floors"]), 4)

if __name__ == "__main__":
    unittest.main()
