import sys
import json
import asyncio
import logging
from typing import Dict, Any, Optional
from mcp.tools_and_resources import MCP_TOOLS_DEFINITIONS, MCP_RESOURCES_DEFINITIONS

logger = logging.getLogger("sentinel.mcp")

class SentinelMCPServer:
    """
    Model Context Protocol (MCP) Server for the Edge Sentinel.
    Allows AEDi, Gemini, Antigravity, and Claude AI agents to interact with
    live network telemetry, speed tests, load shedding, and threat logs.
    """
    def __init__(self, traffic_analyzer, config: Dict[str, Any]):
        self.analyzer = traffic_analyzer
        self.config = config
        self.server_name = "kelvin-drive-sentinel-mcp"
        self.server_version = "2.4.0"

    async def handle_request(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main JSON-RPC 2.0 dispatcher for MCP requests.
        """
        msg_id = request_data.get("id")
        method = request_data.get("method")
        params = request_data.get("params", {})

        if not method:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32600, "message": "Invalid Request: missing method"}}

        # 1. Initialize Handshake
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False}
                    },
                    "serverInfo": {
                        "name": self.server_name,
                        "version": self.server_version
                    }
                }
            }

        # 2. List Tools
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": MCP_TOOLS_DEFINITIONS
                }
            }

        # 3. Call Tool
        elif method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})
            try:
                result_content = await self.execute_tool(tool_name, args)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result_content, indent=2)
                            }
                        ]
                    }
                }
            except Exception as e:
                logger.exception(f"Error executing MCP tool {tool_name}: {e}")
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32000,
                        "message": f"Tool execution failed: {str(e)}"
                    }
                }

        # 4. List Resources
        elif method == "resources/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "resources": MCP_RESOURCES_DEFINITIONS
                }
            }

        # 5. Read Resource
        elif method == "resources/read":
            uri = params.get("uri")
            try:
                res_content = await self.read_resource(uri)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "application/json",
                                "text": json.dumps(res_content, indent=2)
                            }
                        ]
                    }
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32002, "message": f"Resource read failed: {str(e)}"}
                }

        # 6. Ping
        elif method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        }

    async def execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes named MCP tool against live telemetry engine.
        """
        telemetry = await self.analyzer.get_live_telemetry()

        if name == "get_link_stability":
            stab = telemetry["stability"]
            if args.get("detailed", False):
                return {
                    "stability_summary": stab,
                    "probe_targets": telemetry.get("probe_targets", []),
                    "hardware_mains_status": telemetry["hardware_sentinel"]
                }
            return stab

        elif name == "get_traffic_feed":
            floor_id = args.get("floor_id")
            if floor_id:
                matched = [f for f in telemetry["floors"] if f["floor_id"] == floor_id]
                return matched[0] if matched else {"error": f"Floor '{floor_id}' not found"}
            return {
                "total_rx_mbps": telemetry["total_rx_mbps"],
                "total_tx_mbps": telemetry["total_tx_mbps"],
                "wan_interfaces": telemetry["wan_interfaces"],
                "floors": telemetry["floors"]
            }

        elif name == "run_speedtest":
            if args.get("force_fresh_test", False):
                return await self.analyzer.speedtest_engine.run_active_speedtest()
            return telemetry["speedtest"]

        elif name == "get_loadshedding_status":
            return telemetry["loadshedding"]

        elif name == "get_security_alerts":
            limit = args.get("limit", 10)
            return {
                "recent_alerts": self.analyzer.security_analyzer.get_recent_alerts(limit=limit),
                "flagged_ips": self.analyzer.security_analyzer.get_flagged_ips()
            }

        elif name == "get_network_datasheet":
            return {
                "site": self.config.get("site"),
                "network": self.config.get("network"),
                "security_rules": self.config.get("security"),
                "sentinel_mode": self.config.get("sentinel", {}).get("mode", "ALONGSIDE_PASSIVE")
            }

        else:
            raise ValueError(f"Unknown MCP tool: {name}")

    async def read_resource(self, uri: str) -> Dict[str, Any]:
        if uri == "sentinel://kelvin-drive/datasheet":
            return {
                "site": self.config.get("site"),
                "network": self.config.get("network"),
                "security_rules": self.config.get("security")
            }
        elif uri == "sentinel://kelvin-drive/telemetry/live":
            return await self.analyzer.get_live_telemetry()
        else:
            raise ValueError(f"Resource not found: {uri}")

    async def run_stdio_loop(self):
        """
        Runs stdio JSON-RPC server loop for CLI / subagent integration.
        """
        logger.info("[MCP] Stdio loop initialized. Ready for JSON-RPC messages.")
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                req = json.loads(line.decode("utf-8"))
                resp = await self.handle_request(req)
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            except Exception as e:
                err_resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {str(e)}"}}
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()
