"""AEDI - IONITY GLOBAL | watchlist alerts + new-device alerts (v3: fed by the DNS pipeline). Policy 986 AED."""
import asyncio
import time
from types import SimpleNamespace

from app.dns.pipeline import DnsPipeline, DnsRecord
from app.ingest.net_alerts import NetworkWatch
from app.labels import LabelBook
from app.rules.engine import RuleEngine
from app.storage.sqlite_store import SQLiteStore


def _settings(**kw):
    base = dict(watch_ignore="192.168.0.250", watch_hold_s=300, new_device_hold_s=1800, alarm_device="",
                alarm_channel="pwm0", alarm_light_hold_s=20, device_rpc_timeout_s=1, dns_retention_days=7,
                dns_max_rows=0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_watch_and_new_device_alerts(tmp_path):
    async def run():
        store = SQLiteStore(str(tmp_path / "t.db"))
        await store.init()
        try:
            s = _settings()
            rules = RuleEngine(store)
            await rules.load()
            pipe = DnsPipeline(store, rules, LabelBook(tmp_path / "labels.json"), s)
            registry = SimpleNamespace(devices={}, _health=lambda d: "offline")
            nw = NetworkWatch(store, registry, s)
            pipe.flag_listeners.append(nw.on_flag)
            now = time.time()
            pipe.ingest([DnsRecord(ts=now, client_ip="192.168.0.9", qname="m.youtube.com."),
                         DnsRecord(ts=now, client_ip="192.168.0.9", qname="i.ytimg.org"),       # not YouTube
                         DnsRecord(ts=now, client_ip="192.168.0.250", qname="youtube.com")])    # ignored client
            nw.on_new_device("192.168.0.20", "aa-bb-cc-dd-ee-ff", "Living room TV")
            await asyncio.sleep(0.3)
            alerts = await store.list_alerts(open_only=True)
            codes = sorted((a["device_id"], a["code"]) for a in alerts)
            assert codes == [("lan-192.168.0.20", "lan:new"), ("lan-192.168.0.9", "watch:video_streaming:youtube")]
            assert nw.light_on and nw.light_device is None            # no board to light: reported, not raised
            await pipe.flush()
            rows = (await store.query_dns(flagged_only=True))["queries"]
            assert {r["client_ip"] for r in rows} == {"192.168.0.9", "192.168.0.250"}
            await pipe.stop()
            await nw.stop()
        finally:
            await store.close()
    asyncio.run(run())
