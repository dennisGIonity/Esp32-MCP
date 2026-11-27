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
            "loadshedding": {}
        }
        self.engine = LoadSheddingEngine(self.config)
        self.engine.api_key = ""

    def test_no_api_key_is_unavailable_not_guessed(self):
        loop = asyncio.new_event_loop()
        status = loop.run_until_complete(self.engine.get_loadshedding_status())
        loop.close()

        self.assertEqual(status["area_id"], "city-of-johannesburg-block-3")
        self.assertFalse(status["available"])
        self.assertIsNone(status["current_stage"])
        self.assertIsNone(status["next_event"])
        self.assertEqual(status["power_grid_health"], "UNKNOWN")
        self.assertIn("ESKOM_API_KEY", status["reason"])

    def test_api_response_parsing(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone(timedelta(hours=2)))
        raw = {"info": {"name": "Sandton (12)"}, "events": [
            {"start": (now - timedelta(minutes=30)).isoformat(), "end": (now + timedelta(hours=2)).isoformat(),
             "note": "Stage 3"}]}
        s = self.engine._parse_api_response(raw)
        self.assertTrue(s["available"] and s["is_currently_loadshedding"])
        self.assertEqual(s["current_stage"], 3)
        self.assertEqual(s["area_name"], "Sandton (12)")
        raw["events"][0]["note"] = "Load reduction"          # active, no stage stated -> unknown, not 1
        self.assertIsNone(self.engine._parse_api_response(raw)["current_stage"])
        later = {"events": [{"start": (now + timedelta(hours=3)).isoformat(),
                             "end": (now + timedelta(hours=5)).isoformat(), "note": "Stage 2"}]}
        s = self.engine._parse_api_response(later)
        self.assertFalse(s["is_currently_loadshedding"])
        self.assertEqual(s["current_stage"], 0)

    def test_correlation_unknown_power_state(self):
        self.engine._cached_data = self.engine._unavailable("no key")
        corr = self.engine.correlate_link_issue(wan_status="OFFLINE", packet_loss_pct=None, mains_power_ok=True)
        self.assertEqual(corr["root_cause"], "WAN_DOWN_CAUSE_UNKNOWN")
        corr = self.engine.correlate_link_issue(wan_status="UNKNOWN", packet_loss_pct=None, mains_power_ok=True)
        self.assertEqual(corr["root_cause"], "NO_ROUTER_DATA")

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
        self.engine._cached_data = {"available": True, "is_currently_loadshedding": False, "current_stage": 0}
        corr = self.engine.correlate_link_issue(
            wan_status="OFFLINE",
            packet_loss_pct=100.0,
            mains_power_ok=True
        )
        self.assertEqual(corr["root_cause"], "ISP_FIBRE_PHYSICAL_BREAK")
        self.assertEqual(corr["severity"], "CRITICAL")

    def test_correlation_loadshedding_outage(self):
        # Load shedding active, WAN goes down
        self.engine._cached_data = {"available": True, "is_currently_loadshedding": True, "current_stage": 2}
        corr = self.engine.correlate_link_issue(
            wan_status="OFFLINE",
            packet_loss_pct=100.0,
            mains_power_ok=True
        )
        self.assertEqual(corr["root_cause"], "AREA_LOADSHEDDING_SUBSTATION_OUTAGE")

if __name__ == "__main__":
    unittest.main()
