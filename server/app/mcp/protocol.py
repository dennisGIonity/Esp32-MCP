"""
MCP protocol constants shared by the in-process server (app.mcp.server) and the
stdio bridge (server/mcp_stdio_proxy.py), so the two can never disagree about
versions, identity or the handshake shape.
"""
from __future__ import annotations

from typing import Any

# Newest first. The client's requested version is echoed back when we support
# it; otherwise we answer with our newest and the client decides (MCP spec).
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_VERSION = SUPPORTED_VERSIONS[0]

SERVER_NAME = "ionity-esp32-fleet-mcp"
SERVER_VERSION = "2.0.2"

INSTRUCTIONS = (
    "Ionity ESP32-MCP fleet: live telemetry and control for ESP32, Raspberry Pi "
    "Pico and Raspberry Pi Zero/Linux nodes. Start with fleet_summary, then "
    "list_devices / get_device. Metrics are open-ended (temp_c, rssi_dbm, "
    "free_heap_bytes, cpu_pct, ...): read ionity://fleet/schema to see what is "
    "reported. send_command changes device state (reboot, set_meta, set_display "
    "reboot the node) - confirm with the user before using it on 'broadcast'. "
    "Command replies arrive asynchronously: poll get_command_results with the "
    "returned cmd_id. dns_* tools describe LAN DNS traffic seen by the server."
)


def negotiate(requested: str | None) -> str:
    return requested if requested in SUPPORTED_VERSIONS else LATEST_VERSION


def initialize_result(params: dict[str, Any] | None) -> dict[str, Any]:
    requested = (params or {}).get("protocolVersion")
    return {
        "protocolVersion": negotiate(requested),
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
            "prompts": {"listChanged": False},
        },
        "serverInfo": {"name": SERVER_NAME, "title": "Ionity ESP32 Fleet",
                       "version": SERVER_VERSION},
        "instructions": INSTRUCTIONS,
    }
