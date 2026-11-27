"""
AEDI - IONITY GLOBAL | MCP server (JSON-RPC 2.0 dispatcher + sessions)
Doc ID: DOC-2026-10-ESP32MCP-MCP | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Transport-independent. Two transports use it:
  * Streamable HTTP  /mcp (app/mcp/http_transport.py) - sessions, SSE notifications
  * stdio            mcp_stdio_proxy.py -> /mcp (Claude Desktop, Claude Code, Inspector)
and the v2 sessionless JSON endpoint POST /api/v1/mcp/rpc (dashboards, mcpo, curl).

Lifecycle per MCP 2025-11-25: initialize (version negotiation) ->
notifications/initialized -> operation. Resources are subscribable; the server
pushes notifications/resources/updated to subscribed sessions when a flagged
DNS hit arrives, a node goes online/offline, the watchlist or labels change,
and (throttled) when DNS rows or a node's telemetry arrive.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Awaitable, Callable

from app.mcp import protocol
from app.mcp import resources as res
from app.mcp import tools as mcp_tools

log = logging.getLogger("ionity.mcp")

PROTOCOL_VERSION = protocol.LATEST_VERSION
SERVER_NAME = protocol.SERVER_NAME
SERVER_VERSION = protocol.SERVER_VERSION

UNAUTHORIZED = ("This call changes the fleet and needs the admin token: set IONITY_ADMIN_TOKEN for the MCP "
                "bridge, or send 'Authorization: Bearer <token>' over HTTP.")
SESSION_IDLE_S = 3600
THROTTLE_S = 2.0
PROMPTS = res.PROMPTS

# JSON-RPC / MCP error codes
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = -32700, -32600, -32601, -32602, -32603
RESOURCE_NOT_FOUND = -32002


def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code, message, data=None):
    e: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": e}


@dataclass
class Session:
    id: str
    protocol_version: str = protocol.LATEST_VERSION
    client_info: dict = field(default_factory=dict)
    client_capabilities: dict = field(default_factory=dict)
    initialized: bool = False
    subscriptions: set[str] = field(default_factory=set)
    outbox: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1000))
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    streams: int = 0
    dropped: int = 0
    _last_sent: dict[str, float] = field(default_factory=dict)
    _deferred: set[str] = field(default_factory=set)

    def push(self, msg: dict) -> None:
        try:
            self.outbox.put_nowait(msg)
        except asyncio.QueueFull:
            self.dropped += 1


class FleetMCPServer:
    def __init__(self, registry, store, dns=None, integrations=None, hub=None, pipeline=None, rules=None,
                 labels=None):
        self.registry = registry
        self.store = store
        self.dns = dns
        self.integrations = integrations or {}
        self.hub = hub
        self.pipeline = pipeline
        self.rules = rules
        self.labels = labels
        self.sessions: dict[str, Session] = {}
        self._tasks: set[asyncio.Task] = set()
        self._wire()

    # -- wiring: fleet events -> resource notifications -----------------------
    def _wire(self) -> None:
        if self.pipeline is not None:
            self.pipeline.flag_listeners.append(lambda ev, new: self.resource_updated("esp32://dns/flagged",
                                                                                     immediate=True))
        if self.hub is not None:
            self.hub.node_listeners.append(self._on_node)
        if self.rules is not None:
            self.rules.listeners.append(lambda: self.resource_updated("esp32://config/watchlist", immediate=True))
        if self.registry is not None and hasattr(self.registry, "sinks"):
            self.registry.sinks.append(self)

    def _on_node(self, hwid: str, state: str) -> None:
        self.resource_updated("esp32://devices", immediate=True)
        self.resource_updated(f"esp32://devices/{hwid}", immediate=True)

    # registry sink hooks (FleetRegistry._emit)
    def on_telemetry(self, d: dict, metrics: dict, ts: float) -> None:
        did = d.get("device_id")
        if did:
            self.resource_updated(f"esp32://devices/{did}/telemetry")

    def on_state(self, d: dict, state: str, detail: str = "") -> None:
        did = d.get("device_id")
        self.resource_updated("esp32://devices", immediate=True)
        if did:
            self.resource_updated(f"esp32://devices/{did}", immediate=True)

    def on_alert(self, *a) -> None:
        self.resource_updated("esp32://devices")

    def dns_rows_arrived(self) -> None:
        self.resource_updated("esp32://dns/recent")

    async def start(self) -> None:
        self._spawn(self._housekeeping())
        if self.pipeline is not None:
            q = self.pipeline.subscribe()
            self._spawn(self._watch_dns(q))

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        for s in self.sessions.values():
            s.push({"__close__": True})

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _watch_dns(self, q: asyncio.Queue) -> None:
        while True:
            m = await q.get()
            if m.get("type") == "dns":
                self.resource_updated("esp32://dns/recent")
                ip = (m.get("query") or {}).get("client_ip")
                if ip:
                    self.resource_updated(f"esp32://dns/clients/{ip}")

    async def _housekeeping(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = time.time()
            for sid, s in list(self.sessions.items()):
                if s.streams == 0 and now - s.last_seen > SESSION_IDLE_S:
                    self.sessions.pop(sid, None)
                    continue
                for uri in list(s._deferred):
                    if now - s._last_sent.get(uri, 0) >= THROTTLE_S:
                        s._deferred.discard(uri)
                        s._last_sent[uri] = now
                        s.push({"jsonrpc": "2.0", "method": "notifications/resources/updated",
                                "params": {"uri": uri}})

    def resource_updated(self, uri: str, immediate: bool = False) -> None:
        now = time.time()
        for s in list(self.sessions.values()):
            if uri not in s.subscriptions:
                continue
            if immediate or now - s._last_sent.get(uri, 0) >= THROTTLE_S:
                s._last_sent[uri] = now
                s._deferred.discard(uri)
                s.push({"jsonrpc": "2.0", "method": "notifications/resources/updated", "params": {"uri": uri}})
            else:
                s._deferred.add(uri)

    # -- sessions ----------------------------------------------------------------
    def new_session(self) -> Session:
        s = Session(id=secrets.token_urlsafe(24))
        self.sessions[s.id] = s
        return s

    def end_session(self, sid: str) -> bool:
        s = self.sessions.pop(sid, None)
        if s:
            s.push({"__close__": True})
        return s is not None

    def ctx(self, authorized: bool) -> mcp_tools.Ctx:
        return mcp_tools.Ctx(registry=self.registry, store=self.store, hub=self.hub, pipeline=self.pipeline,
                             rules=self.rules, labels=self.labels, dns=self.dns, integrations=self.integrations,
                             authorized=authorized)

    # -- dispatch ----------------------------------------------------------------------
    async def handle(self, req: Any, authorized: bool = True, session: Session | None = None,
                     emit: Callable[[dict], Awaitable[None]] | None = None) -> dict[str, Any] | None:
        """One JSON-RPC message -> response, or None for notifications / client responses.
        `authorized` is False only when an admin token is configured and the caller did not present it."""
        if not isinstance(req, dict):
            return _err(None, INVALID_REQUEST, "Invalid Request: expected a JSON object")
        if req.get("jsonrpc") != "2.0":
            return _err(req.get("id"), INVALID_REQUEST, 'Invalid Request: "jsonrpc" must be "2.0"')
        if "method" not in req:
            return None                                       # a response to a server request: nothing to do
        msg_id = req.get("id")
        method = req.get("method")
        is_notification = "id" not in req
        if not isinstance(method, str) or not method:
            return None if is_notification else _err(msg_id, INVALID_REQUEST, "Invalid Request: missing method")
        if not is_notification and (msg_id is None or isinstance(msg_id, bool) or not isinstance(msg_id, (str, int))):
            return _err(None, INVALID_REQUEST, "Invalid Request: id must be a string or integer")
        params = req.get("params") or {}
        if not isinstance(params, dict):
            return None if is_notification else _err(msg_id, INVALID_PARAMS, "params must be an object")
        if session is not None:
            session.last_seen = time.time()

        if is_notification:
            if method == "notifications/initialized" and session is not None:
                session.initialized = True
            return None                                       # cancelled / progress / roots: accepted, ignored

        try:
            return await self._request(msg_id, method, params, authorized, session, emit)
        except Exception as e:                                # noqa: BLE001 - never a dropped request
            log.exception("MCP %s failed", method)
            return _err(msg_id, INTERNAL, f"internal error: {type(e).__name__}")

    async def _request(self, msg_id, method: str, params: dict, authorized: bool, session: Session | None, emit):
        if method == "initialize":
            if session is not None:
                session.protocol_version = protocol.negotiate(params.get("protocolVersion"))
                session.client_info = params.get("clientInfo") or {}
                session.client_capabilities = params.get("capabilities") or {}
            return _ok(msg_id, protocol.initialize_result(params))
        if method == "ping":
            return _ok(msg_id, {})

        if method == "tools/list":
            return _ok(msg_id, {"tools": mcp_tools.MCP_TOOLS})
        if method == "tools/call":
            return await self._call_tool(msg_id, params, authorized, emit)

        if method == "resources/list":
            return _ok(msg_id, {"resources": res.RESOURCES})
        if method == "resources/templates/list":
            return _ok(msg_id, {"resourceTemplates": res.TEMPLATES})
        if method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str):
                return _err(msg_id, INVALID_PARAMS, "resources/read needs uri")
            try:
                content = await res.read_resource(uri, self.ctx(authorized))
            except res.ResourceNotFound:
                return _err(msg_id, RESOURCE_NOT_FOUND, f"Resource not found: {uri}", {"uri": uri})
            return _ok(msg_id, {"contents": [{"uri": uri, "mimeType": "application/json",
                                              "text": json.dumps(content, indent=2, default=str)}]})
        if method in ("resources/subscribe", "resources/unsubscribe"):
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri.startswith("esp32://"):
                return _err(msg_id, INVALID_PARAMS, "subscribe to an esp32:// resource")
            if session is None:
                return _err(msg_id, INVALID_REQUEST, "subscriptions need a session (POST /mcp with initialize)")
            if method == "resources/subscribe":
                session.subscriptions.add(uri)
            else:
                session.subscriptions.discard(uri)
            return _ok(msg_id, {})

        if method == "prompts/list":
            return _ok(msg_id, {"prompts": res.PROMPTS})
        if method == "prompts/get":
            try:
                desc, msgs = await res.prompt_messages(params.get("name"), params.get("arguments") or {},
                                                       self.ctx(authorized))
            except res.PromptError as e:
                return _err(msg_id, INVALID_PARAMS, str(e))
            return _ok(msg_id, {"description": desc, "messages": msgs})

        if method == "completion/complete":
            vals = await res.complete(params.get("ref") or {}, params.get("argument") or {}, self.ctx(authorized))
            return _ok(msg_id, {"completion": {"values": vals, "total": len(vals), "hasMore": False}})

        return _err(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")

    async def _call_tool(self, msg_id, params: dict, authorized: bool, emit):
        name = params.get("name")
        args = params.get("arguments")
        if name not in mcp_tools.TOOLS:
            return _err(msg_id, INVALID_PARAMS, f"Unknown tool: {name}",
                        {"available": sorted(mcp_tools.TOOLS)})
        token = (params.get("_meta") or {}).get("progressToken")

        async def progress(p: float, msg: str) -> None:
            if emit and token is not None:
                await emit({"jsonrpc": "2.0", "method": "notifications/progress",
                            "params": {"progressToken": token, "progress": p, "total": 1, "message": msg}})

        if mcp_tools.needs_admin(name, args) and not authorized:
            return _ok(msg_id, _tool_result({"error": UNAUTHORIZED}, True))
        await progress(0, f"running {name}")
        try:
            result = await mcp_tools.execute(name, args, self.ctx(authorized))
            is_error = False
        except (mcp_tools.ToolArgError, mcp_tools.ToolFailure) as e:
            result, is_error = {"error": str(e)}, True
        except Exception as e:                                 # noqa: BLE001 - tool faults stay tool errors
            log.exception("tool %s failed", name)
            result, is_error = {"error": f"tool '{name}' failed: {type(e).__name__}: {e}"}, True
        await progress(1, "done")
        return _ok(msg_id, _tool_result(result, is_error))


def _tool_result(result: Any, is_error: bool) -> dict[str, Any]:
    data = json.loads(json.dumps(result, default=str))
    if not isinstance(data, dict):
        data = {"result": data}
    text = data["error"] if is_error and isinstance(data.get("error"), str) else json.dumps(data, indent=2)
    return {"content": [{"type": "text", "text": text}], "structuredContent": data, "isError": is_error}
