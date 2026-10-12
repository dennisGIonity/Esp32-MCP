import unittest
import asyncio
from core.mikrotik_collector import MikroTikCollector
from core.probe_engine import ProbeEngine
from core.loadshedding import LoadSheddingEngine
from core.speedtest_engine import SpeedtestEngine
from core.security_analyzer import SecurityAnalyzer
from core.traffic_analyzer import TrafficAnalyzer
from mcp.mcp_server import SentinelMCPServer

class TestMCPServer(unittest.TestCase):
    def setUp(self):
        self.config = {
            "site": {"id": "test-site", "name": "Kelvin Drive"},
            "network": {},
            "sentinel": {"ping_targets": [{"name": "DNS", "ip": "8.8.8.8"}]},
            "loadshedding": {"mock_if_no_key": True}
        }
        self.collector = MikroTikCollector(self.config)
        self.probe = ProbeEngine(self.config)
        self.ls = LoadSheddingEngine(self.config)
        self.st = SpeedtestEngine(self.config)
        self.sec = SecurityAnalyzer(self.config)
        self.analyzer = TrafficAnalyzer(self.config, self.collector, self.probe, self.ls, self.st, self.sec)
        self.mcp = SentinelMCPServer(self.analyzer, self.config)

    def test_mcp_initialize(self):
        loop = asyncio.new_event_loop()
        resp = loop.run_until_complete(self.mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {}
        }))
        loop.close()

        self.assertEqual(resp["id"], 1)
        self.assertIn("serverInfo", resp["result"])
        self.assertEqual(resp["result"]["serverInfo"]["name"], "kelvin-drive-sentinel-mcp")

    def test_mcp_tools_list(self):
        loop = asyncio.new_event_loop()
        resp = loop.run_until_complete(self.mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }))
        loop.close()

        tools = resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("get_link_stability", tool_names)
        self.assertIn("get_traffic_feed", tool_names)
        self.assertIn("run_speedtest", tool_names)
        self.assertIn("get_loadshedding_status", tool_names)
        self.assertIn("get_security_alerts", tool_names)
        self.assertIn("get_network_datasheet", tool_names)

    def test_mcp_tools_call_stability(self):
        loop = asyncio.new_event_loop()
        resp = loop.run_until_complete(self.mcp.handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_link_stability",
                "arguments": {"detailed": True}
            }
        }))
        loop.close()

        self.assertEqual(resp["id"], 3)
        self.assertIn("content", resp["result"])
        self.assertEqual(resp["result"]["content"][0]["type"], "text")

if __name__ == "__main__":
    unittest.main()
