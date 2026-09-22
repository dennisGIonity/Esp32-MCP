#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | MCP stdio bridge to the live fleet server
Doc ID: DOC-2026-09-ESP32MCP-MCPSTDIO | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

MCP clients that spawn a subprocess (Claude Desktop, Claude Code) speak
JSON-RPC over stdio. The fleet server already speaks MCP over HTTP. This is
the 40-line bridge between the two.

Why a bridge rather than running the MCP server directly against the database:
the fleet server holds the live registry -- which devices are online right
now, current metrics, open alerts, and the MQTT publisher used by
send_command. A second process reading the SQLite file would only ever see a
stale snapshot and could not command a device. Forwarding to the running
server means MCP always reflects reality.

Set IONITY_MCP_URL to point somewhere other than the local default.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("IONITY_MCP_URL", "http://127.0.0.1:8099/api/v1/mcp/rpc")
TIMEOUT = float(os.environ.get("IONITY_MCP_TIMEOUT", "30"))


def log(msg: str) -> None:
    # stderr only: stdout is the JSON-RPC channel and must stay clean.
    print(f"[ionity-mcp-bridge] {msg}", file=sys.stderr, flush=True)


def forward(payload: dict) -> dict | None:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        URL, body, {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}}


def main() -> None:
    log(f"bridging stdio -> {URL}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = req.get("id")
        try:
            resp = forward(req)
        except urllib.error.URLError as e:
            # The fleet server is down. Answer rather than hang, so the client
            # surfaces a useful message instead of a timeout.
            log(f"fleet server unreachable: {e}")
            resp = error(msg_id, -32001,
                         f"Ionity fleet server unreachable at {URL}. "
                         f"Start it with: python server\\run.py")
        except Exception as e:                      # noqa: BLE001
            log(f"bridge error: {e}")
            resp = error(msg_id, -32603, str(e))

        # Notifications (no id) get no response, per JSON-RPC.
        if resp is None or msg_id is None:
            continue
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
