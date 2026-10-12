"""
Smoke tests for the ESP32-MCP fleet server.
    cd server && pytest -q
"""
import asyncio
import time

import pytest

from app.models import TelemetryIn
from app.storage.sqlite_store import SQLiteStore
from app.fleet.registry import FleetRegistry
from app.mcp.server import FleetMCPServer


@pytest.fixture
async def stack(tmp_path):
    store = SQLiteStore(str(tmp_path / "t.db"))
    await store.init()
    reg = FleetRegistry(store)
    await reg.start()
    yield store, reg, FleetMCPServer(reg, store)
    await reg.stop()
    await store.close()


def reading(dev: str, **metrics) -> TelemetryIn:
    return TelemetryIn(device_id=dev, site="s1", group="g1", fw="1.0.0",
                       metrics=metrics or {"temp_c": 25.0, "rssi_dbm": -55})


@pytest.mark.asyncio
async def test_ingest_and_summary(stack):
    _, reg, _ = stack
    for i in range(50):
        reg.ingest(reading(f"esp32-{i:012x}"))
    s = reg.summary()
    assert s.total_devices == 50
    assert s.online == 50


@pytest.mark.asyncio
async def test_open_metrics_are_stored(stack):
    store, reg, _ = stack
    reg.ingest(reading("esp32-aaa", soil_moisture=41.2, door_open=True))
    await asyncio.sleep(1.4)                      # let the writer flush
    rows = await store.query_telemetry(metric="soil_moisture", since_s=0)
    assert rows and rows[0]["value"] == pytest.approx(41.2)
    rows = await store.query_telemetry(metric="door_open", since_s=0)
    assert rows and rows[0]["value"] == 1.0       # bool -> 1/0


@pytest.mark.asyncio
async def test_alert_raise_and_clear(stack):
    store, reg, _ = stack
    reg.ingest(reading("esp32-hot", temp_c=95.0))
    await asyncio.sleep(1.4)
    assert any(a["code"] == "over_temp" for a in await store.list_alerts())

    reg.ingest(reading("esp32-hot", temp_c=30.0))
    await asyncio.sleep(1.4)
    assert not [a for a in await store.list_alerts() if a["device_id"] == "esp32-hot"]


@pytest.mark.asyncio
async def test_health_windows(stack):
    _, reg, _ = stack
    reg.ingest(reading("esp32-old"))
    reg.devices["esp32-old"]["last_seen"] = time.time() - 600
    assert reg.device_view("esp32-old").health == "offline"


@pytest.mark.asyncio
async def test_mcp_dispatch(stack):
    _, reg, mcp = stack
    reg.ingest(reading("esp32-1"))

    init = await mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["serverInfo"]["name"] == "ionity-esp32-fleet-mcp"

    tools = await mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in tools["result"]["tools"]}
    assert {"fleet_summary", "list_devices", "send_command"} <= names

    call = await mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "fleet_summary", "arguments": {}}})
    assert call["result"]["isError"] is False

    bad = await mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})
    assert bad["error"]["code"] == -32601
