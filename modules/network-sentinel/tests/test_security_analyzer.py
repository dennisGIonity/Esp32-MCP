import unittest
from core.security_analyzer import SecurityAnalyzer

class TestSecurityAnalyzer(unittest.TestCase):
    def setUp(self):
        self.config = {
            "security": {
                "blocked_ports": [
                    {"port": 23, "service": "Telnet"},
                    {"port": 22, "service": "SSH"},
                    {"port": 8728, "service": "API"}
                ]
            }
        }
        self.analyzer = SecurityAnalyzer(self.config)

    def test_record_security_event(self):
        alert = self.analyzer.record_security_event(
            attacker_ip="192.0.2.1",
            port=23,
            service="Telnet",
            severity="HIGH",
            message="Test Telnet Probe"
        )
        self.assertEqual(alert["attacker_ip"], "192.0.2.1")
        self.assertEqual(alert["blocked_service"], "Telnet")
        self.assertEqual(alert["severity"], "HIGH")

        recent = self.analyzer.get_recent_alerts(limit=5)
        self.assertGreaterEqual(len(recent), 1)
        self.assertEqual(recent[0]["attacker_ip"], "192.0.2.1")

    def test_flagged_ip_accumulation(self):
        self.analyzer.record_security_event(attacker_ip="198.51.100.22", port=22, service="SSH")
        self.analyzer.record_security_event(attacker_ip="198.51.100.22", port=8728, service="API")

        flagged = self.analyzer.get_flagged_ips()
        matched = [f for f in flagged if f["ip"] == "198.51.100.22"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["hit_count"], 2)
        self.assertIn("SSH", matched[0]["targeted_services"])
        self.assertIn("API", matched[0]["targeted_services"])

if __name__ == "__main__":
    unittest.main()
