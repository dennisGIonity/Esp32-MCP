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
from app.mcp import protocol

log = logging.getLogger("ionity.mcp")

PROTOCOL_VERSION = protocol.LATEST_VERSION
SERVER_NAME = protocol.SERVER_NAME
SERVER_VERSION = protocol.SERVER_VERSION

UNAUTHORIZED = ("This call changes a device and needs the admin token: set IONITY_ADMIN_TOKEN for the MCP "
                "bridge, or send 'Authorization: Bearer <token>' over HTTP.")

PROMPTS = [
    {"name": "fleet_health_check",
     "title": "Fleet health check",
     "description": "Summarise fleet health, list anything stale/offline/alerting and suggest next steps.",
     "arguments": [{"name": "site", "description": "limit to one site", "required": False}]},
    {"name": "investigate_device",
     "title": "Investigate one device",
     "description": "Pull one device's state, history and alerts and explain what is wrong.",
     "arguments": [{"name": "device_id", "description": "device to investigate", "required": True}]},
]


def _prompt_messages(name: str, args: dict) -> list[dict]:
    if name == "fleet_health_check":
        scope = f" for site '{args['site']}'" if args.get("site") else ""
        text = (f"Run fleet_summary{scope}, then list_devices with health=offline, stale and "
                "alerting, and get_alerts. Report: counts, each problem device with its last "
                "seen age and key metrics, likely causes, and concrete next steps. Do not send "
                "any commands without asking me first.")
    elif name == "investigate_device":
        did = args.get("device_id")
        if not did:
            raise ValueError("investigate_device needs device_id")
        text = (f"Investigate device {did}: call get_device (history_points=100) and "
                f"get_alerts for it, look at trends in rssi_dbm, temp_c and free heap/memory, "
                "and explain in plain words what is happening and what to do. Ask before "
                "sending ping or any other command.")
    else:
        raise ValueError(f"Unknown prompt '{name}'")
    return [{"role": "user", "content": {"type": "text", "text": text}}]


def _ok(msg_id, result): return {"jsonrpc": "2.0", "id": msg_id, "result": result}
def _err(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class FleetMCPServer:
    def __init__(self, registry, store, dns=None, integrations=None):
        self.registry = registry
        self.store = store
        self.dns = dns
        self.integrations = integrations or {}

    async def handle(self, req: dict[str, Any], authorized: bool = True) -> dict[str, Any] | None:
        """`authorized` is False only when an admin token is configured and the
        caller did not present it; read-only tools still work, commands do not."""
        if not isinstance(req, dict):
            return _err(None, -32600, "Invalid Request: expected a JSON object")
        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}
        is_notification = "id" not in req

        if not method or not isinstance(method, str):
            return _err(msg_id, -32600, "Invalid Request: missing method")

        if method == "initialize":
            return _ok(msg_id, protocol.initialize_result(params))

        if method.startswith("notifications/") or method == "initialized":
            return None                      # notification: no response
        if is_notification:
            return None                      # never answer a notification

        if method == "ping":
            return _ok(msg_id, {})

        if method == "tools/list":
            return _ok(msg_id, {"tools": mcp_tools.MCP_TOOLS})

        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if mcp_tools.needs_admin(name, args) and not authorized:
                return _ok(msg_id, {"content": [{"type": "text", "text": UNAUTHORIZED}],
                                    "isError": True})
            try:
                result = await mcp_tools.execute(name, args, self.registry,
                                                 self.store, dns=self.dns,
                                                 integrations=self.integrations)
                body = {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
                    # MCP: a tool-level failure MUST set isError so clients do not
                    # treat {"error": ...} as a successful answer.
                    "isError": bool(isinstance(result, dict) and (
                        result.get("ok") is False
                        or (("error" in result) and result.get("ok") is not True
                            and not any(k for k in result if k not in ("error", "device_id", "ok"))))),
                }
                if isinstance(result, dict):
                    body["structuredContent"] = json.loads(json.dumps(result, default=str))
                return _ok(msg_id, body)
            except mcp_tools.ToolArgError as e:
                return _ok(msg_id, {"content": [{"type": "text", "text": str(e)}], "isError": True})
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
            return _ok(msg_id, {"prompts": PROMPTS})

        if method == "prompts/get":
            try:
                name = params.get("name")
                return _ok(msg_id, {"description": next(
                    (p["description"] for p in PROMPTS if p["name"] == name), ""),
                    "messages": _prompt_messages(name, params.get("arguments") or {})})
            except ValueError as e:
                return _err(msg_id, -32602, str(e))

        if method == "resources/templates/list":
            return _ok(msg_id, {"resourceTemplates": []})

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
