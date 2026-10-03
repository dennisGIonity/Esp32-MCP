"""
On-device MCP bridge, Datadog forwarder, sleep state, WAL bound, flasher routes.
AEDI - IONITY GLOBAL | Policy 986 AED
"""
import asyncio
import json
import time

import httpx
import pytest

from app.models import TelemetryIn, StatusIn
from app.storage.sqlite_store import SQLiteStore
from app.fleet.registry import FleetRegistry
from app.mcp.server import FleetMCPServer
from app.integrations.datadog import DatadogForwarder


class FakeBoard:
    """Stands in for fw 2.0 on the far side of the broker: answers the
    JSON-RPC the host publishes, the way DeviceMcp.ino does."""

    def __init__(self, reg: FleetRegistry, silent: bool = False):
        self.reg = reg
        self.silent = silent
        self.seen: list[dict] = []

    async def publish(self, device_id: str, action: str, body: dict) -> bool:
        self.seen.append(body)
        if self.silent:
            return True
        rpc = body.get("rpc", {})
        if rpc.get("method") == "tools/list":
            resp = {"jsonrpc": "2.0", "id": rpc["id"], "result": {"tools": [
                {"name": "read_telemetry", "inputSchema": {"type": "object"}},
                {"name": "set_actuator", "inputSchema": {"type": "object"}}]}}
        elif rpc.get("method") == "tools/call":
            name = rpc["params"]["name"]
            if name == "read_telemetry":
                data = {"metrics": {"free_heap_bytes": 201000, "loop_us_avg": 180}}
                resp = {"jsonrpc": "2.0", "id": rpc["id"], "result": {
                    "content": [{"type": "text", "text": json.dumps(data)}],
                    "structuredContent": data, "isError": False}}
            elif name == "set_actuator":
                data = {"channel": rpc["params"]["arguments"]["channel"], "value": 0.5}
                resp = {"jsonrpc": "2.0", "id": rpc["id"], "result": {
                    "content": [{"type": "text", "text": json.dumps(data)}],
                    "structuredContent": data, "isError": False}}
            else:
                resp = {"jsonrpc": "2.0", "id": rpc["id"],
                        "error": {"code": -32602, "message": f"Unknown tool '{name}'"}}
        else:
            resp = {"jsonrpc": "2.0", "id": rpc.get("id"), "error": {"code": -32601, "message": "nope"}}
        # the reply comes back asynchronously, as it would over MQTT
        asyncio.get_running_loop().call_later(0.02, self.reg.record_cmd_result, {
            "device_id": device_id, "cmd_id": body["cmd_id"], "ok": "error" not in resp,
            "detail": json.dumps(resp)})
        return True


@pytest.fixture
async def stack(tmp_path):
    store = SQLiteStore(str(tmp_path / "t.db"))
    await store.init()
    reg = FleetRegistry(store)
    await reg.start()
    reg.ingest(TelemetryIn(device_id="esp32-aabbccddeeff", site="lab", group="bench", fw="2.0.0",
                           metrics={"temp_c": 40.0, "state_mode": 0}))
    board = FakeBoard(reg)
    reg.command_publisher = board.publish
    yield store, reg, FleetMCPServer(reg, store), board
    await reg.stop()
    await store.close()


async def call(mcp, name, args, authorized=True):
    r = await mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                          "params": {"name": name, "arguments": args}}, authorized=authorized)
    return r["result"]


async def test_device_list_tools_round_trip(stack):
    _, _, mcp, board = stack
    res = await call(mcp, "device_list_tools", {"device_id": "esp32-aabbccddeeff"})
    assert not res["isError"]
    assert res["structuredContent"]["count"] == 2
    assert board.seen[0]["action"] == "mcp" and board.seen[0]["rpc"]["method"] == "tools/list"


async def test_device_call_tool_read_without_admin(stack):
    _, _, mcp, _ = stack
    res = await call(mcp, "device_call_tool", {"device_id": "esp32-aabbccddeeff",
                                               "tool": "read_telemetry"}, authorized=False)
    assert not res["isError"]
    assert res["structuredContent"]["result"]["metrics"]["free_heap_bytes"] == 201000


async def test_device_call_tool_write_needs_admin(stack):
    _, _, mcp, board = stack
    res = await call(mcp, "device_call_tool", {"device_id": "esp32-aabbccddeeff", "tool": "set_actuator",
                                               "arguments": {"channel": "pwm0", "value": 0.5}},
                     authorized=False)
    assert res["isError"] and "admin token" in res["content"][0]["text"]
    assert not board.seen                                   # nothing reached the board
    res = await call(mcp, "device_call_tool", {"device_id": "esp32-aabbccddeeff", "tool": "set_actuator",
                                               "arguments": {"channel": "pwm0", "value": 0.5}})
    assert not res["isError"] and res["structuredContent"]["result"]["channel"] == "pwm0"


async def test_device_call_timeout_is_a_tool_error(stack):
    _, reg, mcp, _ = stack
    silent = FakeBoard(reg, silent=True)
    reg.command_publisher = silent.publish
    res = await call(mcp, "device_call_tool", {"device_id": "esp32-aabbccddeeff",
                                               "tool": "read_telemetry", "timeout_s": 1})
    assert res["isError"] and "no reply" in res["content"][0]["text"]
    assert not reg._waiters                                  # no leaked futures


async def test_sleeping_status_is_not_a_crash(stack):
    _, reg, mcp, _ = stack
    reg.ingest_status(StatusIn(device_id="esp32-aabbccddeeff", site="lab", group="bench",
                               state="sleeping", sleep_s=300, mode="LOW_POWER_SLEEP"))
    v = reg.device_view("esp32-aabbccddeeff")
    assert v.health == "offline" and v.sleeping_until and v.sleeping_until > time.time() + 250
    res = await call(mcp, "device_call_tool", {"device_id": "esp32-aabbccddeeff", "tool": "read_telemetry"})
    assert res["isError"] and "LOW_POWER_SLEEP" in res["content"][0]["text"]


async def test_set_state_mode_command_validation(stack):
    _, _, mcp, board = stack
    res = await call(mcp, "send_command", {"device_id": "esp32-aabbccddeeff", "action": "set_state_mode"})
    assert res["isError"]
    res = await call(mcp, "send_command", {"device_id": "esp32-aabbccddeeff", "action": "set_state_mode",
                                           "mode": "FAILSAFE"})
    assert not res["isError"] and board.seen[-1]["mode"] == "FAILSAFE"


async def test_mode_derived_from_telemetry(stack):
    _, reg, _, _ = stack
    reg.ingest(TelemetryIn(device_id="esp32-m", metrics={"state_mode": 2}))
    assert reg.device_view("esp32-m").mode == "INFERENCE_ACTIVE"


async def test_wal_checkpoint_truncates(stack):
    store, reg, _, _ = stack
    for i in range(300):
        reg.ingest(TelemetryIn(device_id=f"esp32-{i:04d}", metrics={"temp_c": 20.0 + i % 7}))
    await asyncio.sleep(1.5)
    r = await store.checkpoint()
    assert r["busy"] == 0
    import os
    wal = store.path + "-wal"
    assert not os.path.exists(wal) or os.path.getsize(wal) == 0


# ---------------------------------------------------------------------------
# Datadog
# ---------------------------------------------------------------------------
class DDSettings:
    dd_enabled = True
    dd_api_key = "test-key"
    dd_site = "datadoghq.eu"
    dd_env = "test"
    dd_service = "ionity-esp32-mcp"
    dd_metric_prefix = "ionity.esp32"
    dd_tags = "team:iot"
    dd_flush_s = 60


async def test_datadog_forwards_metrics_events_and_checks(stack):
    _, reg, _, _ = stack
    calls: list[tuple[str, dict, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content), dict(request.headers)))
        return httpx.Response(202, json={"errors": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dd = DatadogForwarder(DDSettings(), reg, client=client)
    await dd.start()
    reg.sinks.append(dd)

    reg.ingest(TelemetryIn(device_id="esp32-hot", site="lab", group="bench", fw="2.0.0",
                           metrics={"temp_c": 95.0, "label": "text-is-skipped", "door": True}))
    await asyncio.sleep(1.5)                                  # alert engine runs in the writer
    sent = await dd.flush()
    await dd.stop()

    paths = [c[0] for c in calls]
    assert "/api/v2/series" in paths and "/api/v1/events" in paths and "/api/v1/check_run" in paths
    assert all(c[2]["dd-api-key"] == "test-key" for c in calls)
    series = [s for c in calls if c[0] == "/api/v2/series" for s in c[1]["series"]]
    names = {s["metric"] for s in series}
    assert {"ionity.esp32.temp_c", "ionity.esp32.door", "ionity.fleet.devices"} <= names
    assert "ionity.esp32.label" not in names
    temp = next(s for s in series if s["metric"] == "ionity.esp32.temp_c")
    assert "device_id:esp32-hot" in temp["tags"] and "team:iot" in temp["tags"]
    assert temp["resources"] == [{"name": "esp32-hot", "type": "host"}]
    events = [c[1] for c in calls if c[0] == "/api/v1/events"]
    assert any("over_temp" in e["title"] and e["alert_type"] == "error" for e in events)
    assert sent["points"] > 0 and dd.stats()["errors"] == 0


async def test_datadog_off_without_key(stack):
    _, reg, _, _ = stack
    s = DDSettings(); s.dd_api_key = ""
    dd = DatadogForwarder(s, reg)
    await dd.start()
    dd.on_telemetry({"device_id": "x"}, {"temp_c": 1}, time.time())
    assert dd.stats()["enabled"] is False and dd.stats()["queued_points"] == 0
    await dd.stop()


async def test_datadog_failure_keeps_points_and_backs_off(stack):
    _, reg, _, _ = stack
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="bad key")))
    dd = DatadogForwarder(DDSettings(), reg, client=client)
    dd.on_telemetry({"device_id": "esp32-1"}, {"temp_c": 20.0}, time.time())
    await dd.flush()
    st = dd.stats()
    assert st["errors"] == 1 and "403" in st["last_error"] and st["queued_points"] >= 1
    await client.aclose()


# ---------------------------------------------------------------------------
# Flasher routes
# ---------------------------------------------------------------------------
def test_firmware_and_provisioning_routes(tmp_path, monkeypatch):
    from app.config import settings
    dist = tmp_path / "dist"; dist.mkdir()
    (dist / "esp32s3_uart.bin").write_bytes(b"\xe9" + b"\0" * 31)
    (dist / "manifest.json").write_text(json.dumps({"version": "2.0.0", "builds": [
        {"variant": "esp32s3_uart", "file": "esp32s3_uart.bin"}]}))
    monkeypatch.setattr(settings, "firmware_dist", str(dist))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "f.db"))
    monkeypatch.setattr(settings, "mqtt_enabled", False)
    monkeypatch.setattr(settings, "dns_enabled", False)
    monkeypatch.setattr(settings, "mdns_enabled", False)
    monkeypatch.setattr(settings, "admin_token", "s3cret")
    monkeypatch.setattr(settings, "public_host", "192.168.0.2")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/v1/firmware/manifest").json()["version"] == "2.0.0"
        assert c.get("/api/v1/firmware/esp32s3_uart.bin").content[:1] == b"\xe9"
        assert c.get("/api/v1/firmware/..%2Fmanifest.json").status_code in (400, 404)
        d = c.get("/api/v1/provisioning/defaults").json()
        assert d["server"] == "192.168.0.2" and "fleet_token" not in d and d["admin"] is False
        d = c.get("/api/v1/provisioning/defaults", headers={"Authorization": "Bearer s3cret"}).json()
        assert d["admin"] is True and "fleet_token" in d
        assert c.get("/api/v1/integrations").json()["datadog"]["enabled"] is False
        h = c.get("/api/v1/health").json()
        from app.mcp import protocol as _proto
        assert h["mcp"]["server"] == _proto.SERVER_VERSION and "datadog" in h


def test_stale_pinned_advertise_ip_falls_back():
    from app.ingest import discovery as d
    ip, warn = d.choose_advertise_ip("203.0.113.77")            # TEST-NET-3, never local
    assert ip != "203.0.113.77" and warn and "not on any adapter" in warn
    ip, warn = d.choose_advertise_ip("127.0.0.1")
    assert ip == "127.0.0.1" and warn is None


def test_pinned_ip_prefers_same_subnet(monkeypatch):
    from app.ingest import discovery as d
    monkeypatch.setattr(d, "local_ipv4s", lambda: {"127.0.0.1", "192.168.0.2", "192.168.124.2"})
    monkeypatch.setattr(d, "primary_lan_ip", lambda: "192.168.0.2")
    ip, warn = d.choose_advertise_ip("192.168.124.4")        # lab pin, DHCP gave .2
    assert ip == "192.168.124.2" and "same network" in warn
    ip, warn = d.choose_advertise_ip("10.9.9.9")              # other network entirely
    assert ip == "192.168.0.2" and "not on any adapter" in warn


async def test_set_wifi_command_redacts_password(stack):
    store, reg, mcp, board = stack
    res = await call(mcp, "send_command", {"device_id": "esp32-aabbccddeeff", "action": "set_wifi"})
    assert res["isError"]
    res = await call(mcp, "send_command", {"device_id": "esp32-aabbccddeeff", "action": "set_wifi",
                                           "ssid": "Ionity-LAB_2.4G", "pass": "s3cretpass"})
    assert not res["isError"]
    assert board.seen[-1]["ssid"] == "Ionity-LAB_2.4G" and board.seen[-1]["pass"] == "s3cretpass"
    async with store.db.execute("SELECT payload FROM commands ORDER BY id DESC LIMIT 1") as cur:
        row = await cur.fetchone()
    assert "s3cretpass" not in row[0] and '"pass": "***"' in row[0]
