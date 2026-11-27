#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | MCP stdio bridge to the live fleet server
Doc ID: DOC-2026-09-ESP32MCP-MCPSTDIO | v3.0 | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2

Clients that spawn a subprocess (Claude Desktop, Claude Code, MCP Inspector)
speak MCP over stdio. The fleet server speaks MCP Streamable HTTP at /mcp.
This bridges the two:

  * stdout carries ONLY newline-delimited JSON-RPC; every log line goes to stderr.
  * initialize is answered locally (same constants as the server), so the client
    registers every tool even while the fleet server is down; the bridge opens its
    own backend session lazily and re-opens it after a server restart (HTTP 404).
  * requests run on a small thread pool, so a slow board call never blocks ping
    or a second request; replies are written under one lock.
  * server -> client notifications (notifications/resources/updated ...) are
    relayed from the backend's GET /mcp event stream; resource subscriptions are
    remembered and replayed when the backend session is renewed.
  * the admin token (IONITY_ADMIN_TOKEN, env or .env) is added as a Bearer
    header; the client never sees it.

Why forward instead of opening the database: the fleet server holds the live
registry, the MQTT link to every node and the DNS pipeline. A second process
would only ever see a stale snapshot and could not reach a node.

    IONITY_MCP_URL   default http://127.0.0.1:8099/mcp  (a v2 .../api/v1/mcp/rpc URL is upgraded)
    IONITY_AUTOSTART 1 = start the lab stack once if nothing answers (default)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent            # ...\server


def _find_root() -> Path:
    """Repo root (services/fleet-host/server) or flat kit root (server/), found by markers."""
    env = os.environ.get("IONITY_ROOT")
    if env:
        return Path(env).resolve()
    for d in HERE.parents:
        if any((d / m).exists() for m in (".env", ".env.example", "SETUP.cmd", ".git")):
            return d
    return HERE.parent


ROOT = _find_root()
sys.path.insert(0, str(HERE))


def log(msg: str) -> None:
    # stderr only: stdout is the JSON-RPC channel and must stay clean.
    print(f"[ionity-mcp-bridge] {msg}", file=sys.stderr, flush=True)


def _read_env_file() -> dict[str, str]:
    """Minimal .env reader (the bridge must not need pydantic to start)."""
    out: dict[str, str] = {}
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _url() -> str:
    u = os.environ.get("IONITY_MCP_URL", "http://127.0.0.1:8099/mcp").rstrip("/")
    if u.endswith("/api/v1/mcp/rpc"):                # v2 Claude Desktop configs
        u = u[: -len("/api/v1/mcp/rpc")] + "/mcp"
    return u


URL = _url()
TIMEOUT = float(os.environ.get("IONITY_MCP_TIMEOUT", "35"))
AUTOSTART = os.environ.get("IONITY_AUTOSTART", "1") != "0"
ADMIN_TOKEN = os.environ.get("IONITY_ADMIN_TOKEN") or _read_env_file().get("IONITY_ADMIN_TOKEN", "")

try:                                              # one source of truth for the handshake and manifest
    from app.mcp import protocol as _protocol
except Exception:                                 # noqa: BLE001
    _protocol = None


def initialize_result(params: dict | None) -> dict:
    if _protocol:
        return _protocol.initialize_result(params)
    return {"protocolVersion": (params or {}).get("protocolVersion") or "2025-11-25",
            "capabilities": {"tools": {"listChanged": False},
                             "resources": {"subscribe": True, "listChanged": False},
                             "prompts": {"listChanged": False}},
            "serverInfo": {"name": "ionity-esp32-fleet-mcp", "version": "3.0.0"}}


def local_manifest(method: str) -> dict:
    """What tools/list etc. answer while the backend is down - imported from the server
    package, so it can never drift from what the backend implements."""
    try:
        if method == "tools/list":
            from app.mcp.tools import MCP_TOOLS
            return {"tools": MCP_TOOLS}
        from app.mcp import resources as R
        if method == "resources/list":
            return {"resources": R.RESOURCES}
        if method == "resources/templates/list":
            return {"resourceTemplates": R.TEMPLATES}
        if method == "prompts/list":
            return {"prompts": R.PROMPTS}
    except Exception as e:                        # noqa: BLE001
        log(f"could not import the local manifest ({e}); advertising nothing")
    key = {"tools/list": "tools", "resources/list": "resources", "resources/templates/list": "resourceTemplates",
           "prompts/list": "prompts"}[method]
    return {key: []}


def _start_script() -> str:
    for p in [HERE, *HERE.parents][:7]:
        for rel in (("tools", "scripts", "start_lab.ps1"), ("scripts", "start_lab.ps1"), ("START.cmd",)):
            c = p.joinpath(*rel)
            if c.exists():
                return str(c)
    return str(ROOT / "tools" / "scripts" / "start_lab.ps1")


UNREACHABLE = ("The Ionity fleet server is not running, so live fleet data is unavailable. Start it with:  "
               "powershell -ExecutionPolicy Bypass -File " + _start_script())


class BackendDown(Exception):
    pass


class Bridge:
    def __init__(self) -> None:
        self.out_lock = threading.Lock()
        self.sess_lock = threading.Lock()
        self.sid: str | None = None
        self.version = "2025-11-25"
        self.client_init: dict = {}
        self.subscriptions: set[str] = set()
        self.autostart_tried = False
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mcp-call")
        self.listener: threading.Thread | None = None

    # -- stdout --------------------------------------------------------------
    def write(self, msg: dict) -> None:
        line = json.dumps(msg, separators=(",", ":"), ensure_ascii=False)
        with self.out_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    # -- HTTP ----------------------------------------------------------------
    def _headers(self, with_session: bool = True) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": self.version}
        if ADMIN_TOKEN:
            h["Authorization"] = f"Bearer {ADMIN_TOKEN}"
        if with_session and self.sid:
            h["Mcp-Session-Id"] = self.sid
        return h

    def _post(self, payload: dict, *, with_session: bool = True, timeout: float | None = None):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(URL, body, self._headers(with_session), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
                raw = r.read()
                return r.status, dict(r.headers), (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                data = json.loads(raw) if raw else None
            except ValueError:
                data = None
            return e.code, dict(e.headers), data
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise BackendDown(str(e)) from None

    def ensure_session(self, force: bool = False) -> None:
        with self.sess_lock:
            if self.sid and not force:
                return
            init = {"jsonrpc": "2.0", "id": "bridge-init", "method": "initialize",
                    "params": {"protocolVersion": self.client_init.get("protocolVersion", self.version),
                               "capabilities": self.client_init.get("capabilities") or {},
                               "clientInfo": self.client_init.get("clientInfo")
                               or {"name": "ionity-mcp-bridge", "version": "3.0.0"}}}
            self.sid = None
            status, headers, data = self._post(init, with_session=False, timeout=10)
            sid = next((v for k, v in headers.items() if k.lower() == "mcp-session-id"), None)
            if status != 200 or not sid:
                raise BackendDown(f"backend initialize failed (HTTP {status})")
            self.sid = sid
            self.version = (data or {}).get("result", {}).get("protocolVersion", self.version)
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=10)
            for uri in sorted(self.subscriptions):
                self._post({"jsonrpc": "2.0", "id": f"bridge-sub-{uri}", "method": "resources/subscribe",
                            "params": {"uri": uri}}, timeout=10)
            log(f"backend session open ({URL})")
        self.start_listener()

    def forward(self, msg: dict):
        """POST with the session; re-open the session once if the server lost it."""
        self.ensure_session()
        status, _h, data = self._post(msg)
        if status == 404:
            log("backend session expired (server restarted?) - reopening")
            self.ensure_session(force=True)
            status, _h, data = self._post(msg)
        if status == 202:
            return None
        if data is None:
            raise BackendDown(f"HTTP {status} without a body")
        return data

    # -- server -> client notifications --------------------------------------
    def start_listener(self) -> None:
        if self.listener and self.listener.is_alive():
            return
        self.listener = threading.Thread(target=self._listen, name="mcp-sse", daemon=True)
        self.listener.start()

    def _listen(self) -> None:
        delay = 1.0
        while not self.stop.is_set():
            sid = self.sid
            if not sid:
                time.sleep(delay)
                continue
            req = urllib.request.Request(URL, headers={**self._headers(), "Accept": "text/event-stream"},
                                         method="GET")
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    delay = 1.0
                    data_lines: list[str] = []
                    for raw in r:
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                        elif line == "" and data_lines:
                            try:
                                self.write(json.loads("\n".join(data_lines)))
                            except ValueError:
                                pass
                            data_lines = []
                        if self.stop.is_set() or self.sid != sid:
                            break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    try:
                        self.ensure_session(force=True)
                    except BackendDown:
                        pass
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
            time.sleep(delay)
            delay = min(delay * 2, 30.0)

    # -- autostart -------------------------------------------------------------
    def try_autostart(self) -> None:
        if self.autostart_tried or not AUTOSTART:
            return
        self.autostart_tried = True
        lab = Path(_start_script())
        flags = 0x00000008 | 0x08000000 if os.name == "nt" else 0      # DETACHED | NO_WINDOW
        try:
            if lab.suffix == ".ps1" and lab.exists() and os.name == "nt":
                log("fleet server not running - starting the lab stack")
                subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle",
                                  "Hidden", "-File", str(lab), "-Quiet"], cwd=str(ROOT), creationflags=flags,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                py = ROOT / ".venv" / ("Scripts/pythonw.exe" if os.name == "nt" else "bin/python")
                run = HERE / "run.py"
                if not py.exists() or not run.exists():
                    log("autostart skipped: venv or run.py not found")
                    return
                log("fleet server not running - starting it")
                subprocess.Popen([str(py), str(run)], cwd=str(ROOT), creationflags=flags,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=os.name != "nt")
        except Exception as e:                     # noqa: BLE001
            log(f"autostart failed: {e}")
            return
        for _ in range(30):
            time.sleep(1)
            try:
                self.ensure_session(force=True)
                log("fleet server is up")
                return
            except BackendDown:
                continue
        log("fleet server did not come up in time")

    # -- messages ---------------------------------------------------------------
    def handle(self, req: dict) -> dict | None:
        msg_id = req.get("id")
        method = req.get("method")
        if method is None:
            return None                            # a client response: nothing to relay
        is_notification = "id" not in req
        if method == "initialize":
            self.client_init = req.get("params") or {}
            self.version = initialize_result(self.client_init)["protocolVersion"]
            threading.Thread(target=self._warm_up, daemon=True).start()
            return {"jsonrpc": "2.0", "id": msg_id, "result": initialize_result(self.client_init)}
        if is_notification:
            if method == "notifications/initialized":
                return None
            try:
                self.forward(req)                  # cancelled etc.
            except BackendDown:
                pass
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "resources/subscribe":
            self.subscriptions.add((req.get("params") or {}).get("uri", ""))
        elif method == "resources/unsubscribe":
            self.subscriptions.discard((req.get("params") or {}).get("uri", ""))
        try:
            resp = self.forward(req)
            if resp is not None:
                return resp
        except BackendDown as e:
            log(f"backend unreachable at {URL}: {e}")
            if method in ("tools/list", "resources/list", "resources/templates/list", "prompts/list"):
                return {"jsonrpc": "2.0", "id": msg_id, "result": local_manifest(method)}
            if method in ("resources/subscribe", "resources/unsubscribe"):
                return {"jsonrpc": "2.0", "id": msg_id, "result": {}}   # replayed when the backend returns
            threading.Thread(target=self.try_autostart, daemon=True).start()
            if method == "tools/call":
                # A tool error, not a protocol error: the client shows the sentence instead of
                # dropping the whole server.
                return {"jsonrpc": "2.0", "id": msg_id,
                        "result": {"content": [{"type": "text", "text": UNREACHABLE}], "isError": True}}
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32001, "message": UNREACHABLE}}
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": "empty reply from the server"}}

    def _warm_up(self) -> None:
        try:
            self.ensure_session()
        except BackendDown:
            self.try_autostart()

    def run_one(self, req: dict) -> None:
        try:
            resp = self.handle(req)
        except Exception as e:                     # noqa: BLE001 - the bridge must answer
            log(f"bridge error: {e}")
            resp = None if "id" not in req else {"jsonrpc": "2.0", "id": req.get("id"),
                                                  "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            self.write(resp)

    def main(self) -> None:
        log(f"bridging stdio -> {URL}")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            items = msg if isinstance(msg, list) else [msg]
            for item in items:
                if not isinstance(item, dict):
                    self.write({"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32600, "message": "Invalid Request"}})
                    continue
                if item.get("method") in ("initialize", "ping") or "id" not in item:
                    self.run_one(item)             # ordered: the handshake comes first
                else:
                    self.pool.submit(self.run_one, item)
        self.stop.set()
        self.pool.shutdown(wait=True)
        if self.sid:
            try:
                urllib.request.urlopen(urllib.request.Request(URL, headers=self._headers(), method="DELETE"),
                                       timeout=3).close()
            except Exception:                      # noqa: BLE001
                pass


def main() -> None:
    Bridge().main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
