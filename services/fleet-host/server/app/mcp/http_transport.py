"""
AEDI - IONITY GLOBAL | MCP Streamable HTTP transport
Doc ID: DOC-2026-10-ESP32MCP-MCPHTTP | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

MCP 2025-11-25 "Streamable HTTP", co-hosted with the REST / WebSocket API:

  POST   /mcp   one JSON-RPC message (batches only for 2025-03-26 and older)
                - initialize creates a session: response header Mcp-Session-Id
                - requests -> application/json, or text/event-stream when the client
                  accepts it AND asked for progress (_meta.progressToken)
                - notifications / responses only -> 202 Accepted
  GET    /mcp   text/event-stream of server -> client notifications for the session
                (notifications/resources/updated ...), keep-alive comment every 15 s
  DELETE /mcp   end the session

  POST   /api/v1/mcp/rpc   v2 sessionless JSON endpoint (dashboard console, mcpo, curl)

Security (spec "Security Warning"): the Origin header is validated (DNS
rebinding), MCP-Protocol-Version is checked, sessions are unguessable
(192-bit), write tools need the admin token, and POSTs are rate limited.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import settings
from app.mcp import protocol
from app.security import is_admin, mcp_limiter, rate_limit

router = APIRouter()
SID = "mcp-session-id"
KEEPALIVE_S = 15.0


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin or (origin == "null" and request.client and request.client.host in ("127.0.0.1", "::1")):
        return True
    try:
        o = urlsplit(origin)
    except ValueError:
        return False
    host = (o.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    allowed = {x.strip().rstrip("/") for x in settings.mcp_allowed_origins.split(",") if x.strip()}
    if origin.rstrip("/") in allowed or "*" in allowed:
        return True
    # same origin as the server itself (the portal / dashboard served from this host)
    req_host = request.headers.get("host", "")
    return bool(req_host) and o.netloc.lower() == req_host.lower()


def _rpc_error(code: int, msg: str, status: int, msg_id: Any = None) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": msg}}, status_code=status)


def _version_ok(request: Request, session) -> str | None:
    v = request.headers.get("mcp-protocol-version")
    if v is None:
        return None
    if v not in protocol.SUPPORTED_VERSIONS:
        return f"unsupported MCP-Protocol-Version '{v}' (supported: {', '.join(protocol.SUPPORTED_VERSIONS)})"
    if session is not None and session.initialized and v != session.protocol_version:
        return f"MCP-Protocol-Version '{v}' differs from the negotiated '{session.protocol_version}'"
    return None


def _sse(msg: dict) -> bytes:
    return f"event: message\ndata: {json.dumps(msg, default=str)}\n\n".encode()


@router.post("/mcp")
async def mcp_post(request: Request):
    if not _origin_ok(request):
        return _rpc_error(-32600, "Origin not allowed (set IONITY_MCP_ALLOWED_ORIGINS)", 403)
    rate_limit(request, mcp_limiter)
    server = request.app.state.mcp
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        return _rpc_error(-32700, "Parse error", 400)
    if body is None:
        return _rpc_error(-32600, "empty body", 400)
    sid = request.headers.get(SID)
    msgs = body if isinstance(body, list) else [body]
    if not msgs:
        return _rpc_error(-32600, "empty batch", 400)
    is_init = any(isinstance(m, dict) and m.get("method") == "initialize" for m in msgs)

    session = None
    if is_init:
        if len(msgs) != 1:
            return _rpc_error(-32600, "initialize must be sent on its own", 400)
        session = server.new_session()
    elif sid:
        session = server.sessions.get(sid)
        if session is None:
            return _rpc_error(-32600, "unknown or expired session - initialize again", 404)
    else:
        return _rpc_error(-32600, "missing Mcp-Session-Id header (send initialize first)", 400)

    err = _version_ok(request, session)
    if err:
        return _rpc_error(-32600, err, 400)
    if isinstance(body, list) and session.protocol_version not in protocol.BATCH_VERSIONS:
        return _rpc_error(-32600, f"JSON-RPC batches are not part of MCP {session.protocol_version}", 400)

    admin = is_admin(request)
    requests = [m for m in msgs if isinstance(m, dict) and "method" in m and "id" in m]
    if not requests:
        for m in msgs:
            await server.handle(m, authorized=admin, session=session)
        return Response(status_code=202)

    headers = {"Mcp-Session-Id": session.id} if is_init else {}
    accept = request.headers.get("accept", "")
    wants_progress = any(((m.get("params") or {}).get("_meta") or {}).get("progressToken") is not None
                         for m in requests if isinstance(m.get("params"), dict))
    if "text/event-stream" in accept and wants_progress and not isinstance(body, list):
        q: asyncio.Queue = asyncio.Queue()

        async def emit(msg: dict) -> None:
            await q.put(msg)

        async def run() -> None:
            try:
                resp = await server.handle(body, authorized=admin, session=session, emit=emit)
                await q.put(resp)
            finally:
                await q.put(None)

        task = asyncio.create_task(run())

        async def stream():
            try:
                while True:
                    m = await q.get()
                    if m is None:
                        break
                    yield _sse(m)
            finally:
                if not task.done():
                    task.cancel()
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={**headers, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    out = [r for r in [await server.handle(m, authorized=admin, session=session) for m in msgs] if r is not None]
    payload: Any = out if isinstance(body, list) else (out[0] if out else None)
    if payload is None:
        return Response(status_code=202, headers=headers)
    return JSONResponse(payload, headers=headers)


@router.get("/mcp")
async def mcp_get(request: Request):
    if not _origin_ok(request):
        return _rpc_error(-32600, "Origin not allowed", 403)
    if "text/event-stream" not in request.headers.get("accept", ""):
        return Response(status_code=405, headers={"Allow": "POST, DELETE"})
    server = request.app.state.mcp
    session = server.sessions.get(request.headers.get(SID, ""))
    if session is None:
        return _rpc_error(-32600, "unknown or missing Mcp-Session-Id", 404 if request.headers.get(SID) else 400)
    err = _version_ok(request, session)
    if err:
        return _rpc_error(-32600, err, 400)

    async def stream():
        session.streams += 1
        try:
            yield b": ionity mcp stream open\n\n"
            while True:
                try:
                    m = await asyncio.wait_for(session.outbox.get(), KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"
                    continue
                if m.get("__close__"):
                    break
                yield _sse(m)
        finally:
            session.streams -= 1
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/mcp")
async def mcp_delete(request: Request):
    if not _origin_ok(request):
        return _rpc_error(-32600, "Origin not allowed", 403)
    server = request.app.state.mcp
    sid = request.headers.get(SID)
    if not sid:
        return _rpc_error(-32600, "missing Mcp-Session-Id", 400)
    if not server.end_session(sid):
        return _rpc_error(-32600, "unknown session", 404)
    return Response(status_code=200)


@router.post("/api/v1/mcp/rpc")
async def mcp_rpc_legacy(request: Request) -> Any:
    """v2 endpoint: sessionless, JSON only, batches allowed (dashboard console, mcpo, scripts)."""
    if not _origin_ok(request):
        return _rpc_error(-32600, "Origin not allowed", 403)
    rate_limit(request, mcp_limiter)
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
    if body is None:
        return _rpc_error(-32600, "empty body", 400)
    server = request.app.state.mcp
    admin = is_admin(request)
    if isinstance(body, list):
        if not body:
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "empty batch"}}
        out = [r for r in [await server.handle(b, authorized=admin) for b in body] if r is not None]
        return out if out else Response(status_code=202)
    resp = await server.handle(body, authorized=admin)
    return resp if resp is not None else Response(status_code=202)
