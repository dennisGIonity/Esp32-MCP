"""AEDI - IONITY GLOBAL | v2.1 network-wide alerts. Policy 986 AED."""
import asyncio
import time
from types import SimpleNamespace

from app.ingest.net_alerts import NetworkWatch
from app.storage.sqlite_store import SQLiteStore


def _settings(**kw):
    base = dict(watch_domains="youtube.com,tiktok.com", watch_ignore="192.168.0.250",
                watch_hold_s=300, new_device_hold_s=1800, alarm_device="",
                alarm_channel="pwm0", alarm_light_hold_s=20, device_rpc_timeout_s=1)
    base.update(kw)
    return SimpleNamespace(**base)


def test_watch_and_new_device_alerts(tmp_path):
    async def run():
        store = SQLiteStore(str(tmp_path / "t.db"))
        await store.init()
        registry = SimpleNamespace(devices={}, _health=lambda d: "offline")
        nw = NetworkWatch(store, registry, _settings())
        assert nw.matched("www.youtube.com.") == "youtube.com"
        assert nw.matched("notyoutube.com") is None
        nw.on_query({"client_ip": "192.168.0.9", "qname": "m.youtube.com", "ts": time.time()})
        nw.on_query({"client_ip": "192.168.0.9", "qname": "i.ytimg.org", "ts": time.time()})
        nw.on_query({"client_ip": "192.168.0.250", "qname": "youtube.com", "ts": time.time()})
        nw.on_new_device("192.168.0.20", "aa-bb-cc-dd-ee-ff", "Living room TV")
        await asyncio.sleep(0.2)
        alerts = await store.list_alerts(open_only=True)
        codes = sorted((a["device_id"], a["code"]) for a in alerts)
        assert codes == [("lan-192.168.0.20", "lan:new"), ("lan-192.168.0.9", "watch:youtube.com")]
        assert nw.light_on and nw.light_error            # no board online: reported, not raised
        rows = await store.network_overview(0)
        assert rows == [] or isinstance(rows, list)
        await store.close()
    asyncio.run(run())
