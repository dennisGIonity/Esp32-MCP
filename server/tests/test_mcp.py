"""
MCP server contract tests: handshake, validation, auth, notifications, prompts,
HTTP transport and the stdio bridge's offline behaviour.
    cd server && pytest -q
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.models import TelemetryIn
from app.storage.sqlite_store import SQLiteStore
from app.fleet.registry import FleetRegistry
from app.mcp.server import FleetMCPServer
from app.mcp import tools as T
from app.mcp import protocol


@pytest.fixture
async def mcp(tmp_path):
    store = SQLiteStore(str(tmp_path / "m.db"))
    await store.init()
    reg = FleetRegistry(store)
    await reg.start()
    reg.ingest(TelemetryIn(device_id="esp32-aaa", site="lab", group="bench",
                           metrics={"temp_c": 30.5, "rssi_dbm": -50}))
    yield FleetMCPServer(reg, store), reg
    await reg.stop()
    await store.close()


def rpc(i, method, params=None):
    m = {"jsonrpc": "2.0", "id": i, "method": method}
    if params is not None:
        m["params"] = params
    return m


def text(resp):
    return resp["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_version_negotiation(mcp):
    srv, _ = mcp
    for v in protocol.SUPPORTED_VERSIONS:
        r = await srv.handle(rpc(1, "initialize", {"protocolVersion": v}))
        assert r["result"]["protocolVersion"] == v
    r = await srv.handle(rpc(2, "initialize", {"protocolVersion": "1999-01-01"}))
    assert r["result"]["protocolVersion"] == protocol.LATEST_VERSION
    assert "instructions" in r["result"] and "fleet_summary" in r["result"]["instructions"]


@pytest.mark.asyncio
async def test_notifications_get_no_reply(mcp):
    srv, _ = mcp
    assert await srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert await srv.handle({"jsonrpc": "2.0", "method": "notifications/cancelled",
                             "params": {"requestId": 1}}) is None
    assert await srv.handle({"jsonrpc": "2.0", "method": "tools/list"}) is None  # no id


@pytest.mark.asyncio
async def test_every_tool_has_annotations_and_schema():
    names = set()
    for t in T.MCP_TOOLS:
        assert t["name"] not in names, "duplicate tool name"
        names.add(t["name"])
        assert t["inputSchema"]["type"] == "object"
        a = t["annotations"]
        assert a["readOnlyHint"] is (t["name"] not in T.MAY_WRITE_TOOLS)
    assert T.TOOL_INDEX["send_command"]["annotations"]["destructiveHint"] is True


@pytest.mark.asyncio
async def test_missing_and_bad_args_are_tool_errors(mcp):
    srv, _ = mcp
    r = await srv.handle(rpc(1, "tools/call", {"name": "get_device", "arguments": {}}))
    assert r["result"]["isError"] and "device_id" in text(r)
    r = await srv.handle(rpc(2, "tools/call", {"name": "list_devices",
                                                "arguments": {"health": "sleepy"}}))
    assert r["result"]["isError"] and "must be one of" in text(r)
    r = await srv.handle(rpc(3, "tools/call", {"name": "no_such_tool", "arguments": {}}))
    assert r["result"]["isError"] and "Unknown tool" in text(r)
    r = await srv.handle(rpc(4, "tools/call", {"name": "send_command",
                                                "arguments": {"device_id": "esp32-aaa",
                                                              "action": "set_meta"}}))
    assert r["result"]["isError"] and "set_meta needs" in text(r)


def test_limits_are_clamped_not_rejected():
    out = T.validate_args("list_devices", {"limit": "999999", "offset": -5})
    assert out["limit"] == 2000 and out["offset"] == 0
    out = T.validate_args("send_command", {"device_id": "x", "action": "dns_probe",
                                            "names": "a.com, b.com"})
    assert out["names"] == ["a.com", "b.com"]


@pytest.mark.asyncio
async def test_structured_content_and_data(mcp):
    srv, _ = mcp
    r = await srv.handle(rpc(1, "tools/call", {"name": "get_device",
                                                "arguments": {"device_id": "esp32-aaa"}}))
    assert r["result"]["isError"] is False
    assert r["result"]["structuredContent"]["device"]["metrics"]["temp_c"] == 30.5
    r = await srv.handle(rpc(2, "resources/read", {"uri": "ionity://fleet/schema"}))
    schema = json.loads(r["result"]["contents"][0]["text"])
    assert "temp_c" in schema["metrics"]


@pytest.mark.asyncio
async def test_send_command_requires_admin_when_configured(mcp):
    srv, _ = mcp
    args = {"name": "send_command", "arguments": {"device_id": "esp32-aaa", "action": "ping"}}
    r = await srv.handle(rpc(1, "tools/call", args), authorized=False)
    assert r["result"]["isError"] and "admin token" in text(r)
    # reads still work unauthorised
    r = await srv.handle(rpc(2, "tools/call", {"name": "fleet_summary", "arguments": {}}),
                         authorized=False)
    assert r["result"]["isError"] is False
    # authorised: no MQTT publisher in tests -> clear, non-crashing failure
    r = await srv.handle(rpc(3, "tools/call", args), authorized=True)
    assert r["result"]["isError"] is True and "MQTT" in text(r)


@pytest.mark.asyncio
async def test_prompts(mcp):
    srv, _ = mcp
    lst = await srv.handle(rpc(1, "prompts/list"))
    assert {p["name"] for p in lst["result"]["prompts"]} == {"fleet_health_check", "investigate_device"}
    got = await srv.handle(rpc(2, "prompts/get", {"name": "investigate_device",
                                                   "arguments": {"device_id": "esp32-aaa"}}))
    assert "esp32-aaa" in got["result"]["messages"][0]["content"]["text"]
    bad = await srv.handle(rpc(3, "prompts/get", {"name": "investigate_device"}))
    assert bad["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_invalid_request_shapes(mcp):
    srv, _ = mcp
    assert (await srv.handle(["not", "a", "dict"]))["error"]["code"] == -32600
    assert (await srv.handle({"jsonrpc": "2.0", "id": 9}))["error"]["code"] == -32600


def test_http_transport_auth_and_notifications(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.config import settings
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "h.db"))
    monkeypatch.setattr(settings, "mqtt_enabled", False)
    monkeypatch.setattr(settings, "dns_enabled", False)
    monkeypatch.setattr(settings, "mdns_enabled", False)
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    from app.main import app
    with TestClient(app) as c:
        # notification -> 202, empty body
        r = c.post("/api/v1/mcp/rpc", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert r.status_code == 202 and r.content == b""
        # parse error
        r = c.post("/api/v1/mcp/rpc", content=b"{nope", headers={"Content-Type": "application/json"})
        assert r.json()["error"]["code"] == -32700
        # command without / with token
        call = rpc(1, "tools/call", {"name": "send_command",
                                     "arguments": {"device_id": "broadcast", "action": "ping"}})
        assert "admin token" in c.post("/api/v1/mcp/rpc", json=call).json()["result"]["content"][0]["text"]
        ok = c.post("/api/v1/mcp/rpc", json=call, headers={"Authorization": "Bearer s3cret"}).json()
        assert "admin token" not in ok["result"]["content"][0]["text"]
        assert c.post("/api/v1/devices/broadcast/cmd", json={"action": "ping"}).status_code == 401
        h = c.get("/api/v1/health").json()
        assert h["admin_token_required"] is True and h["mcp"]["tools"] == len(T.MCP_TOOLS)


def test_stdio_bridge_survives_dead_backend():
    """The bridge must complete the handshake and list tools with no server."""
    proxy = Path(__file__).resolve().parents[1] / "mcp_stdio_proxy.py"
    msgs = [rpc(1, "initialize", {"protocolVersion": "2025-06-18"}),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            rpc(2, "tools/list"), rpc(3, "prompts/list"),
            rpc(4, "tools/call", {"name": "fleet_summary", "arguments": {}})]
    env = {"IONITY_MCP_URL": "http://127.0.0.1:9/api/v1/mcp/rpc", "IONITY_AUTOSTART": "0",
           "IONITY_MCP_TIMEOUT": "2", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}
    p = subprocess.run([sys.executable, str(proxy)], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       capture_output=True, text=True, timeout=60, env=env)
    out = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    by_id = {o["id"]: o for o in out}
    assert set(by_id) == {1, 2, 3, 4}, p.stderr               # no reply to the notification
    assert by_id[1]["result"]["protocolVersion"] == "2025-06-18"
    assert len(by_id[2]["result"]["tools"]) == len(T.MCP_TOOLS)
    assert by_id[4]["result"]["isError"] is True
