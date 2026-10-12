"""Regression tests for the 2026-10-02 audit fixes."""
import asyncio
import time

import pytest

from app.models import TelemetryIn
from app.storage.sqlite_store import SQLiteStore
from app.fleet.registry import FleetRegistry
from app.ingest import dns_resolver as dr


@pytest.fixture
async def stack(tmp_path):
    store = SQLiteStore(str(tmp_path / "a.db"))
    await store.init()
    reg = FleetRegistry(store)
    await reg.start()
    yield store, reg
    await reg.stop()
    await store.close()


@pytest.mark.asyncio
async def test_buffered_replay_is_backdated(stack):
    """A board replaying its offline buffer sets age_ms; rows must land at
    their real times, not in one same-second burst."""
    store, reg = stack
    now = time.time()
    for i in range(5):
        reg.ingest(TelemetryIn(device_id="esp32-replay", age_ms=(4 - i) * 10_000,
                               metrics={"temp_c": 20 + i}))
    for _ in range(40):
        if reg.queue.empty():
            break
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.2)                      # let the writer commit
    rows = await store.query_telemetry(device_id="esp32-replay", limit=10)
    ts = sorted(r["ts"] for r in rows)
    assert len(ts) == 5
    assert all(9.5 <= (b - a) <= 10.5 for a, b in zip(ts, ts[1:]))
    assert now - 41 <= ts[0] <= now - 39
    dev = await store.list_devices()
    assert dev[0]["msg_count"] == 5              # batched upsert kept the count


def test_bogus_device_clock_is_clamped():
    t = TelemetryIn(device_id="esp32-x", ts=5.0)   # 1970: unset RTC
    assert abs(t.at() - time.time()) < 1
    t2 = TelemetryIn(device_id="esp32-x", ts=time.time() - 30)
    assert abs((time.time() - 30) - t2.at()) < 1




@pytest.mark.asyncio
async def test_prune_uses_ts_index(stack):
    store, _ = stack
    async with store.db.execute("EXPLAIN QUERY PLAN DELETE FROM telemetry_metric WHERE ts < 1") as cur:
        plan = " ".join(str(tuple(r)) for r in await cur.fetchall())
    assert "idx_tm_ts" in plan


class _Settings:
    dns_upstreams = "127.0.0.1"
    dns_timeout_s = 0.2
    dns_allow_from = "192.168.0.0/16,127.0.0.0/8"
    dns_bind = "127.0.0.1"
    dns_port = 0
    dns_retention_days = 1


def test_resolver_refuses_foreign_clients():
    svc = dr.DnsService(store=None, settings=_Settings())
    assert svc._client_allowed("192.168.1.20")
    assert svc._client_allowed("127.0.0.1")
    assert not svc._client_allowed("8.8.8.8")
    assert not svc._client_allowed("not-an-ip")


def test_resolver_stats_include_guardrails():
    svc = dr.DnsService(store=None, settings=_Settings())
    st = svc.stats()
    assert st["refused_clients"] == 0 and st["dropped_overload"] == 0
    assert "192.168.0.0/16" in st["allow_from"]
