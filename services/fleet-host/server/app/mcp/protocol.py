"""
MCP protocol constants shared by the in-process server (app.mcp.server) and the
stdio bridge (server/mcp_stdio_proxy.py), so the two can never disagree about
versions, identity or the handshake shape.
"""
from __future__ import annotations

from typing import Any

# Newest first. The client's requested version is echoed back when we support
# it; otherwise we answer with our newest and the client decides (MCP spec).
# 2026-07-28 (stateless, server/discover) clients fall back to initialize.
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
LATEST_VERSION = SUPPORTED_VERSIONS[0]
# JSON-RPC batches were removed from MCP in 2025-06-18.
BATCH_VERSIONS = ("2025-03-26", "2024-11-05")

SERVER_NAME = "ionity-esp32-fleet-mcp"
SERVER_VERSION = "3.0.0"

INSTRUCTIONS = (
    "Ionity ESP32-MCP fleet: ESP32 and Raspberry Pi nodes, and the LAN DNS they observe. "
    "Fleet: fleet_summary -> list_devices -> get_device_telemetry. "
    "LAN activity: query_dns_activity (filter by domain_pattern, client, device_label, category, time) and "
    "get_flagged_alerts ('who opened YouTube in the last 30 minutes' = category youtube, minutes 30). "
    "Change things only when the user asks: manage_dns_watchlist, manage_device_labels and "
    "trigger_node_action need the admin token; reboot / LED / forget need confirm=true - ask the user first. "
    "Subscribe to esp32://dns/flagged to be told about new watchlist hits. "
    "Only monitor networks the user manages, with the users' knowledge."
)


def negotiate(requested: str | None) -> str:
    return requested if requested in SUPPORTED_VERSIONS else LATEST_VERSION


def capabilities() -> dict[str, Any]:
    return {
        "tools": {"listChanged": False},
        "resources": {"subscribe": True, "listChanged": False},
        "prompts": {"listChanged": False},
        "completions": {},
    }


def initialize_result(params: dict[str, Any] | None) -> dict[str, Any]:
    requested = (params or {}).get("protocolVersion")
    return {
        "protocolVersion": negotiate(requested),
        "capabilities": capabilities(),
        "serverInfo": {"name": SERVER_NAME, "title": "Ionity ESP32 Fleet", "version": SERVER_VERSION,
                       "websiteUrl": "https://www.ionity.today"},
        "instructions": INSTRUCTIONS,
    }
