#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | MCP stdio bridge to the live fleet server
Doc ID: DOC-2026-09-ESP32MCP-MCPSTDIO | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

MCP clients that spawn a subprocess (Claude Desktop, Claude Code) speak
JSON-RPC over stdio. The fleet server speaks MCP over HTTP. This bridges them.

Why forward instead of opening the database directly: the fleet server holds
the live registry -- which devices are online right now, current metrics, open
alerts, and the MQTT publisher that send_command needs. A second process
reading the SQLite file would only ever see a stale snapshot and could not
command a device.

MUST SURVIVE A DEAD BACKEND.
The MCP client performs an `initialize` handshake the moment it starts. If
that handshake fails, the whole server is marked Failed and none of its tools
appear -- even after the backend comes back. So the handshake and the tool
manifest are answered locally, from the same definitions the server uses, and
only actual tool *calls* need the backend. A down backend then costs you one
clear error message per call instead of the entire integration.

It will also try to start the fleet server once, if it isn't running.
Set IONITY_AUTOSTART=0 to disable that.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent            # ...\server
ROOT = HERE.parent                                # ...\.ESP32-MCP
sys.path.insert(0, str(HERE))

URL = os.environ.get("IONITY_MCP_URL", "http://127.0.0.1:8099/api/v1/mcp/rpc")
TIMEOUT = float(os.environ.get("IONITY_MCP_TIMEOUT", "30"))
AUTOSTART = os.environ.get("IONITY_AUTOSTART", "1") != "0"

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ionity-esp32-fleet-mcp"
SERVER_VERSION = "1.0.0"

_autostart_tried = False


def log(msg: str) -> None:
    # stderr only: stdout is the JSON-RPC channel and must stay clean.
    print(f"[ionity-mcp-bridge] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Local manifest - one source of truth, imported from the server package so it
# can never drift from what the backend actually implements.
# ---------------------------------------------------------------------------
def local_manifest() -> tuple[list, list]:
    try:
        from app.mcp.tools import MCP_TOOLS, MCP_RESOURCES
        return MCP_TOOLS, MCP_RESOURCES
    except Exception as e:                        # noqa: BLE001
        log(f"could not import tool definitions ({e}); advertising nothing")
        return [], []


def backend_up() -> bool:
    health = URL.replace("/api/v1/mcp/rpc", "/api/v1/health")
    try:
        with urllib.request.urlopen(health, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def try_autostart() -> None:
    """Start the fleet server once, detached, if nothing is listening."""
    global _autostart_tried
    if _autostart_tried or not AUTOSTART:
        return
    _autostart_tried = True
    if backend_up():
        return

    flags = 0x00000008 | 0x08000000                # DETACHED | NO_WINDOW
    lab = ROOT / "scripts" / "start_lab.ps1"
    try:
        if lab.exists():
            # Bring up the whole lab (broker + server + serial bridge), not
            # just the server - commands need the broker too.
            log("fleet server not running - starting the lab stack")
            subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                              "-WindowStyle", "Hidden", "-File", str(lab), "-Quiet"],
                             cwd=str(ROOT), creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
            py = ROOT / ".venv" / "Scripts" / "python.exe"
            exe = pyw if pyw.exists() else (py if py.exists() else None)
            run = HERE / "run.py"
            if not exe or not run.exists():
                log("autostart skipped: venv or run.py not found")
                return
            log("fleet server not running - starting it")
            subprocess.Popen([str(exe), str(run)], cwd=str(ROOT), creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:                         # noqa: BLE001
        log(f"autostart failed: {e}")
        return

    for _ in range(30):                            # up to ~30s to come up
        time.sleep(1)
        if backend_up():
            log("fleet server is up")
            return
    log("fleet server did not come up in time")


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------
def ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def err(msg_id, code: int, message: str):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def forward(payload: dict) -> dict | None:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        URL, body, {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


UNREACHABLE = (
    "The Ionity fleet server is not running, so live fleet data is "
    "unavailable. Start it with:  E:\\.ESP32-MCP\\scripts\\start_fleet.ps1"
)


def handle(req: dict) -> dict | None:
    msg_id = req.get("id")
    method = req.get("method")

    # --- answered locally: these must never depend on the backend ---------
    if method == "initialize":
        try_autostart()
        return ok(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return ok(msg_id, {})

    if method == "prompts/list":
        return ok(msg_id, {"prompts": []})

    if method in ("tools/list", "resources/list"):
        # Prefer the backend (authoritative), fall back to local definitions
        # so the client still registers every tool while the server is down.
        try:
            resp = forward(req)
            if resp is not None:
                return resp
        except Exception:
            pass
        tools, resources = local_manifest()
        if method == "tools/list":
            log("backend down - serving the local tool manifest")
            return ok(msg_id, {"tools": tools})
        return ok(msg_id, {"resources": resources})

    # --- everything else genuinely needs live data ------------------------
    try:
        return forward(req)
    except urllib.error.URLError:
        try_autostart()
        try:
            return forward(req)                    # one retry after autostart
        except Exception:
            pass
        log(f"backend unreachable at {URL}")
        if method == "tools/call":
            # Surface it as a tool error, not a protocol error, so the client
            # shows the message to the user instead of dropping the server.
            return ok(msg_id, {
                "content": [{"type": "text", "text": UNREACHABLE}],
                "isError": True,
            })
        return err(msg_id, -32001, UNREACHABLE)
    except Exception as e:                         # noqa: BLE001
        log(f"bridge error: {e}")
        return err(msg_id, -32603, str(e))


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

        resp = handle(req)
        if resp is None or req.get("id") is None:
            continue
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
