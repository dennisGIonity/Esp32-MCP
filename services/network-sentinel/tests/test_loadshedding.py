import unittest
import asyncio
from core.loadshedding import LoadSheddingEngine

class TestLoadSheddingEngine(unittest.TestCase):
    def setUp(self):
        self.config = {
            "site": {
                "location": {
                    "eskom_area_id": "city-of-johannesburg-block-3",
                    "eskom_area_name": "Sandton / Kelvin Drive"
                }
            },
            "loadshedding": {"mock_if_no_key": True}
        }
        self.engine = LoadSheddingEngine(self.config)

    def test_mock_status_generation(self):
        loop = asyncio.new_event_loop()
        status = loop.run_until_complete(self.engine.get_loadshedding_status())
        loop.close()

        self.assertEqual(status["area_id"], "city-of-johannesburg-block-3")
        self.assertIn("current_stage", status)
        self.assertIn("next_event", status)

    def test_correlation_mains_power_loss(self):
        corr = self.engine.correlate_link_issue(
            wan_status="ONLINE",
            packet_loss_pct=0.0,
            mains_power_ok=False # Mains failure
        )
        self.assertEqual(corr["root_cause"], "ONSITE_MAINS_POWER_LOSS")
        self.assertEqual(corr["severity"], "CRITICAL")

    def test_correlation_isp_fibre_break(self):
        # Grid normal, but WAN completely down
        self.engine._cached_data = {"is_currently_loadshedding": False, "current_stage": 0}
        corr = self.engine.correlate_link_issue(
            wan_status="OFFLINE",
            packet_loss_pct=100.0,
            mains_power_ok=True
        )
        self.assertEqual(corr["root_cause"], "ISP_FIBRE_PHYSICAL_BREAK")
        self.assertEqual(corr["severity"], "CRITICAL")

    def test_correlation_loadshedding_outage(self):
        # Load shedding active, WAN goes down
        self.engine._cached_data = {"is_currently_loadshedding": True, "current_stage": 2}
        corr = self.engine.correlate_link_issue(
            wan_status="OFFLINE",
            packet_loss_pct=100.0,
            mains_power_ok=True
        )
        self.assertEqual(corr["root_cause"], "AREA_LOADSHEDDING_SUBSTATION_OUTAGE")

if __name__ == "__main__":
    unittest.main()
