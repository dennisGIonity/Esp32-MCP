"""
JSON-RPC 2.0 MCP server for the Ionity ESP32 fleet.

Two transports, one dispatcher:
  * HTTP  POST /api/v1/mcp/rpc     (for AEDi, dashboards, curl)
  * stdio  python -m app.mcp.server (for Claude Desktop / Claude Code)

Protocol shape follows the RouterProject SentinelMCPServer, widened from one
device to a whole fleet.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from app.mcp import tools as mcp_tools

log = logging.getLogger("ionity.mcp")

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ionity-esp32-fleet-mcp"
SERVER_VERSION = "1.0.0"


def _ok(msg_id, result): return {"jsonrpc": "2.0", "id": msg_id, "result": result}
def _err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class FleetMCPServer:
    def __init__(self, registry, store, dns=None):
        self.registry = registry
        self.store = store
        self.dns = dns

    async def handle(self, req: dict[str, Any]) -> dict[str, Any] | None:
        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        if not method:
            return _err(msg_id, -32600, "Invalid Request: missing method")

        if method == "initialize":
            return _ok(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })

        if method in ("notifications/initialized", "initialized"):
            return None                      # notification: no response

        if method == "ping":
            return _ok(msg_id, {})

        if method == "tools/list":
            return _ok(msg_id, {"tools": mcp_tools.MCP_TOOLS})

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                result = await mcp_tools.execute(name, args, self.registry,
                                                 self.store, dns=self.dns)
                return _ok(msg_id, {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
                    "isError": False,
                })
            except Exception as e:
                log.exception("tool %s failed", name)
                return _ok(msg_id, {
                    "content": [{"type": "text", "text": f"Tool '{name}' failed: {e}"}],
                    "isError": True,
                })

        if method == "resources/list":
            return _ok(msg_id, {"resources": mcp_tools.MCP_RESOURCES})

        if method == "resources/read":
            uri = params.get("uri")
            try:
                content = await mcp_tools.read_resource(uri, self.registry, self.store)
                return _ok(msg_id, {"contents": [{
                    "uri": uri, "mimeType": "application/json",
                    "text": json.dumps(content, indent=2, default=str),
                }]})
            except Exception as e:
                return _err(msg_id, -32002, str(e))

        if method == "prompts/list":
            return _ok(msg_id, {"prompts": []})

        return _err(msg_id, -32601, f"Method not found: {method}")


# ---------------------------------------------------------------------------
# stdio entrypoint for MCP clients that spawn a subprocess
# ---------------------------------------------------------------------------
async def _stdio_main() -> None:
    from app.config import settings
    from app.storage.sqlite_store import SQLiteStore
    from app.fleet.registry import FleetRegistry

    store = SQLiteStore(settings.sqlite_path)
    await store.init()
    registry = FleetRegistry(store)
    await registry.start()
    server = FleetMCPServer(registry, store)

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = await server.handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()

    await registry.stop()
    await store.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    asyncio.run(_stdio_main())
