"""
AEDI - IONITY GLOBAL | Fleet host NetObserve: rule engine, labels, DNS pipeline, EWP/1 hub, REST.
Frames are produced with the Pi agent's own codec (devices/pi-agent), decoded here with cbor2:
two independent implementations of packages/protocol must agree.   Policy 986 AED
"""
import asyncio
import json
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.dns.pipeline import DnsPipeline, DnsRecord
from app.ewp import ewp_protocol as P
from app.ewp.hub import EwpHub
from app.fleet.registry import FleetRegistry
from app.labels import LabelBook, norm_mac
from app.rules.engine import RuleEngine, RuleError, normalize_pattern
from app.storage.sqlite_store import SQLiteStore

AGENT_DIR = next(p / "devices" / "pi-agent" for p in Path(__file__).resolve().parents
                 if (p / "devices" / "pi-agent" / "ewp_cbor.py").exists())
sys.path.insert(0, str(AGENT_DIR))
import ewp_cbor as cb  # noqa: E402  (the Pi agent's stdlib codec)

HW = "esp32-98a316e5d18c"
MAC_TV = bytes.fromhex("3c22fb010203")


def settings(**kw):
    base = dict(watch_hold_s=300, dns_retention_days=7, dns_max_rows=0, device_rpc_timeout_s=2)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
async def host(tmp_path):
    store = SQLiteStore(str(tmp_path / "h.db"))
    await store.init()
    reg = FleetRegistry(store)
    await reg.start()
    rules = RuleEngine(store)
    await rules.load()
    labels_path = tmp_path / "device_labels.json"
    labels_path.write_text(json.dumps({"3c:22:fb:01:02:03": {"label": "Kitchen TV"}}))
    labels = LabelBook(labels_path)
    s = settings()
    pipe = DnsPipeline(store, rules, labels, s)
    await pipe.start()
    hub = EwpHub(reg, pipe, rules, s)
    sent = []
    hub.publisher = lambda topic, frame, qos, retain: sent.append((topic, frame)) or True
    yield SimpleNamespace(store=store, reg=reg, rules=rules, labels=labels, pipe=pipe, hub=hub, sent=sent,
                          tmp=tmp_path)
    await hub.stop()
    await pipe.stop()
    await reg.stop()
    await store.close()


def hello(watch_ver=1, tools=(P.TOOL_READ_TELEMETRY, P.TOOL_SET_WATCHLIST, P.TOOL_SET_ACTUATOR)):
    return cb.encode_frame(P.MSG_HELLO, cb.keyed("HelloBody", {
        "proto": [1, 1], "hwid": HW, "uuid": b"\x01" * 16,
        "fw": cb.keyed("FirmwareInfo", {"version": "3.1.0", "elfSha": b"\x00" * 8, "idf": "v5.5.5"}),
        "chip": cb.keyed("ChipInfo", {"model": P.CHIP_ESP32_S3, "rev": 2, "cores": 2, "flashMb": 4, "psramMb": 8}),
        "limits": cb.keyed("LinkLimits", {"maxDownlink": 4096, "maxUplink": 2048, "maxInflight": 4}),
        "tools": [[t, 1, 0] for t in tools], "models": [],
        "actuators": [cb.keyed("ActuatorDescriptor", {"ch": 0, "name": "pwm0", "kind": P.ACT_PWM, "gpio": 5,
                                                      "safe": 0})],
        "metrics": [], "tasks": [], "mode": 0, "site": "lab", "group": "bench", "label": "lab-node-01",
        "resetReason": 1, "bootCount": 3,
        "net": cb.keyed("NetObserveInfo", {"mode": 1, "port": 53, "upstreams": [socket.inet_aton("1.1.1.1")],
                                           "caps": 1, "watchVer": watch_ver, "watchRules": 13})}))


def dns_batch(seq=1):
    return cb.encode_frame(P.MSG_EVENT, cb.keyed("EventBody", {"kind": P.EVENT_DNS_BATCH, "dns": cb.keyed("DnsBatch", {
        "seq": seq, "windowMs": 10_000,
        "clients": [[socket.inet_aton("192.168.0.42"), MAC_TV, "kitchen-tv"],
                    [socket.inet_aton("192.168.0.43"), b"", ""]],
        "queries": [[1500, 0, "www.youtube.com", 1, 0, 12, 3, P.DNSQ_FLAGGED, 1],
                    [1400, 0, "rr1---sn-abc.googlevideo.com", 1, 0, 20, 40, P.DNSQ_FLAGGED, 1],
                    [900, 1, "example.org", 28, 0, 8, 1, 0, 0]]})}), rid=seq)


# ---- rule engine -----------------------------------------------------------------
async def test_rule_engine_matching_and_crud(host):
    r = host.rules
    assert r.match("www.youtube.com").category.name == "video_streaming:youtube"
    assert r.match("rr5---sn-x.googlevideo.com").rule.pattern == "*.googlevideo.com"
    assert r.match("googlevideo.com") is None                     # *.x = subdomains only
    assert r.match("notyoutube.com") is None and r.match("youtube.com.evil.org") is None
    assert r.match("youtubei.googleapis.com").category.name == "video_streaming:youtube"
    v0 = r.version
    rule = await r.add_rule("instagram.com", "social_media")
    assert r.match("i.instagram.com").category.name == "social_media" and r.version != v0
    rule2 = await r.add_rule("music.youtube.com", "music")
    assert r.match("music.youtube.com").rule.id == rule2.id      # most specific wins
    assert r.match("www.youtube.com").category.name == "video_streaming:youtube"
    with pytest.raises(RuleError):
        await r.add_rule("https://x.com/a", "x")
    with pytest.raises(RuleError):
        normalize_pattern("com")
    out = await r.remove_rule("youtube.com")                      # built in: disabled, not deleted
    assert "disabled" in out and r.match("www.youtube.com") is None
    await r.set_enabled("youtube.com", True)
    await r.remove_rule(rule.id)
    assert r.match("instagram.com") is None
    # device frames: REPLACE first, all fit the downlink limit, version carried
    frames = r.device_frames()
    K = P.KEYS["SetWatchlistArgs"]
    assert frames[0][K["op"]] == P.WATCH_OP_REPLACE and frames[0][K["version"]] == r.version
    assert all(len(cb.encode_frame(P.MSG_REQ, f)) <= P.LIMITS["downlinkFrame"] for f in frames)


async def test_rule_engine_splits_big_lists_and_survives_restart(host):
    for i in range(110):
        await host.rules.add_rule(f"site{i:03d}-with-a-rather-long-name.example-domain.com", "custom:bulk")
    frames = host.rules.device_frames()
    K = P.KEYS["SetWatchlistArgs"]
    assert len(frames) > 1 and all(f[K["op"]] == P.WATCH_OP_APPEND for f in frames[1:])
    assert sum(len(f[K["rules"]]) for f in frames) == len(host.rules.device_rules())
    assert all(len(cb.encode_frame(P.MSG_REQ, f)) <= P.LIMITS["downlinkFrame"] for f in frames)
    again = RuleEngine(host.store)
    await again.load()
    assert again.version == host.rules.version


# ---- labels --------------------------------------------------------------------------
def test_labels_hot_reload_and_edit(tmp_path):
    p = tmp_path / "device_labels.json"
    p.write_text(json.dumps({"3C-22-FB-01-02-03": {"label": "Kitchen TV"}, "192.168.0.77": {"label": "Printer"},
                             "pico-c18354253344139d": {"label": "lab-node-03", "site": "lab"}}))
    book = LabelBook(p)
    assert book.resolve(mac="3c:22:fb:01:02:03") == ("Kitchen TV", "file-mac")
    assert book.resolve(ip="192.168.0.77") == ("Printer", "file-ip")
    assert book.resolve(ip="192.168.0.99", hostname="phone") == ("phone", "hostname")
    assert book.node("pico-c18354253344139d")["site"] == "lab"
    book.set("3c:22:fb:01:02:03", "Lounge TV")
    assert book.resolve(mac="3c-22-fb-01-02-03")[0] == "Lounge TV"
    assert sum(1 for e in book.entries() if e["kind"] == "mac") == 1   # one entry per MAC, any spelling
    p.write_text(p.read_text().replace("Printer", "Office printer"))  # hand edit -> picked up
    book._checked = 0
    import os
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert book.resolve(ip="192.168.0.77")[0] == "Office printer"
    with pytest.raises(ValueError):
        book.set("not-a-key!", "x")
    assert norm_mac("AA:BB:CC:DD:EE:FF") == "aa-bb-cc-dd-ee-ff" and norm_mac("zz") is None


# ---- hub + pipeline ------------------------------------------------------------------------
async def test_ewp_node_hello_telemetry_and_dns_batch(host):
    hub, reg = host.hub, host.reg
    assert hub.handle(f"ionity/ewp1/lab/{HW}/hello", hello())
    await asyncio.sleep(0.05)
    d = reg.devices[HW]
    assert d["protocol"] == "ewp1" and d["fw"] == "3.1.0" and "set_watchlist" in d["tools"]
    # node runs the built-in list (v1), the host's differs -> the host pushes its own
    assert host.sent and host.sent[0][0] == f"ionity/ewp1/lab/{HW}/dn"
    hdr, body = cb.decode_frame(host.sent[0][1])
    assert hdr["type"] == P.MSG_REQ and hdr["tool"] == P.TOOL_SET_WATCHLIST
    assert body[P.KEYS["SetWatchlistArgs"]["version"]] == host.rules.version
    # the node answers, the hub marks it in sync
    hub.handle(f"ionity/ewp1/lab/{HW}/rs", cb.encode_frame(P.MSG_RES, cb.keyed("ResultBody", {
        "timing": cb.keyed("ExecTiming", {"queueUs": 5, "execUs": 300, "heapDelta": 0}),
        "result": cb.keyed("WatchlistResult", {"version": host.rules.version, "rules": 13, "categories": 2})}),
        rid=hdr["rid"], tool=P.TOOL_SET_WATCHLIST))
    await asyncio.sleep(0.05)
    assert d.get("watch_synced") == host.rules.version
    # status + telemetry -> registry
    hub.handle(f"ionity/ewp1/lab/{HW}/st", cb.encode_frame(P.MSG_STATUS, cb.keyed("StatusBody", {
        "state": P.STATE_ONLINE, "ip": socket.inet_aton("192.168.0.50")})))
    hub.handle(f"ionity/ewp1/lab/{HW}/tm", cb.encode_frame(P.MSG_TELEMETRY, cb.keyed("TelemetryBody", {
        "seq": 1, "uptimeS": 99, "mode": 0,
        "heap": cb.keyed("HeapStats", {"internalFree": 180000, "internalMinFree": 150000, "internalLargest": 90000}),
        "net": cb.keyed("NetStats", {"rssiDbm": -61, "txFail": 0, "reconnects": 1, "uplinkQueued": 0}),
        "cpu": [12, 30],
        "dns": cb.keyed("DnsCounters", {"mode": 1, "queries": 44, "forwarded": 40, "cacheHits": 4,
                                        "upstreamFail": 0, "dropped": 0, "flagged": 43, "clients": 2,
                                        "lanDevices": 3})})))
    v = reg.device_view(HW)
    assert v.health == "online" and v.ip == "192.168.0.50" and v.metrics["free_heap_bytes"] == 180000
    assert v.metrics["rssi_dbm"] == -61 and v.metrics["cpu_pct"] == 21 and v.metrics["dns_queries"] == 44
    assert v.metrics["heap_fragmentation"] == 0.5 and v.kind == "esp32" and v.mac == "98-a3-16-e5-d1-8c"
    # DNS batch -> attributed, flagged, stored
    assert hub.handle(f"ionity/ewp1/lab/{HW}/ev", dns_batch())
    await asyncio.sleep(0.1)
    await host.pipe.flush()
    q = await host.store.query_dns(flagged_only=True)
    assert {(r["qname"], r["label"], r["count"]) for r in q["queries"]} == {
        ("www.youtube.com", "Kitchen TV", 3), ("rr1---sn-abc.googlevideo.com", "Kitchen TV", 40)}
    assert all(r["node_id"] == HW and r["mac"] == "3c-22-fb-01-02-03" for r in q["queries"])
    flags = await host.store.list_flags()
    assert len(flags) == 1 and flags[0]["hits"] == 43 and flags[0]["category"] == "video_streaming:youtube"
    other = await host.store.query_dns(client_ip="192.168.0.43")
    assert other["queries"][0]["flagged"] == 0 and other["queries"][0]["qtype"] == "AAAA"


async def test_immediate_flag_is_not_double_counted(host):
    hub = host.hub
    hub.handle(f"ionity/ewp1/lab/{HW}/hello", hello(watch_ver=0, tools=(P.TOOL_READ_TELEMETRY,)))
    flag = cb.encode_frame(P.MSG_EVENT, cb.keyed("EventBody", {"kind": P.EVENT_DNS_FLAGGED, "flag": cb.keyed("DnsFlag", {
        "client": [socket.inet_aton("192.168.0.42"), MAC_TV, "kitchen-tv"], "qname": "www.youtube.com", "qtype": 1,
        "category": 1, "rule": "youtube.com", "ageMs": 20, "watchVer": 1})}))
    events = []
    host.pipe.flag_listeners.append(lambda ev, new: events.append((ev, new)))
    assert hub.handle(f"ionity/ewp1/lab/{HW}/ev", flag)
    await asyncio.sleep(0.05)
    assert events and events[0][1] is True and events[0][0]["label"] == "Kitchen TV"
    hub.handle(f"ionity/ewp1/lab/{HW}/ev", dns_batch())            # the same query again in the batch
    await asyncio.sleep(0.1)
    flags = await host.store.list_flags()
    assert flags[0]["hits"] == 43                                 # 1 immediate + 2 + 40, not 44


async def test_bad_frames_are_counted_not_fatal(host):
    hub = host.hub
    assert not hub.handle(f"ionity/ewp1/lab/{HW}/tm", b"\xe5\x01garbage")
    assert not hub.handle("ionity/ewp1/lab/NOT-A-HWID/tm", dns_batch())
    assert not hub.handle(f"ionity/ewp1/lab/{HW}/rs", dns_batch())         # EVENT on rs: wrong channel
    assert not hub.handle(f"ionity/ewp1/lab/{HW}/dn", dns_batch())         # downlink channel from a node
    assert hub.stats["bad_frames"] >= 3 and hub.stats["wrong_channel"] == 1


async def test_tool_call_round_trip_and_errors(host):
    hub = host.hub
    hub.handle(f"ionity/ewp1/lab/{HW}/hello", hello(watch_ver=0, tools=(P.TOOL_READ_TELEMETRY, P.TOOL_SET_ACTUATOR)))
    host.sent.clear()

    async def node():
        while not host.sent:
            await asyncio.sleep(0.01)
        hdr, body = cb.decode_frame(host.sent[-1][1])
        assert body == {1: 0, 2: 1.0, 4: 5000}
        hub.handle(f"ionity/ewp1/lab/{HW}/rs", cb.encode_frame(P.MSG_ERR, cb.keyed("ErrorBody", {
            "detail": "FAILSAFE: outputs locked"}), rid=hdr["rid"], tool=hdr["tool"], status=P.ST_FAILSAFE_LOCKED))
    t = asyncio.create_task(node())
    r = await hub.call(HW, "set_actuator", {"channel": 0, "value": 1.0, "leaseMs": 5000})
    await t
    assert r["ok"] is False and r["status"] == "FAILSAFE_LOCKED" and "locked" in r["error"]
    r = await hub.call(HW, "scan_lan", {})
    assert r["ok"] is False and "does not offer" in r["error"]
    r = await hub.call(HW, "read_telemetry", {}, timeout_s=0.2)
    assert r["ok"] is False and r["status"] == "TIMEOUT"
    hdr, _ = cb.decode_frame(host.sent[-1][1])
    assert hdr["type"] == P.MSG_CANCEL                            # the host cancels what timed out


async def test_pi_agent_frames_decode_on_the_host(host, tmp_path, monkeypatch):
    """The real Pi agent builds HELLO/TELEMETRY; the host's hub accepts them unchanged."""
    import ionity_agent as ia
    monkeypatch.setattr(ia, "FS", tmp_path)
    cfg = ia.load_config(None)
    cfg["state_file"] = str(tmp_path / "state.json")
    agent = ia.Agent(cfg)
    agent.hwid = "pi-a1b2c3d4"
    assert host.hub.handle_frame("lab", agent.hwid, "hello", cb.encode_frame(P.MSG_HELLO, agent.hello()))
    assert host.hub.handle_frame("lab", agent.hwid, "tm", cb.encode_frame(P.MSG_TELEMETRY, agent.telemetry_body()))
    v = host.reg.device_view("pi-a1b2c3d4")
    assert v.kind == "pi" and v.protocol == "ewp1" and v.fw == ia.FW_VERSION and v.health == "online"


async def test_retention_prunes_by_age_and_row_cap(host):
    now = time.time()
    host.pipe.ingest([DnsRecord(ts=now - 10 * 86400, client_ip="192.168.0.5", qname="old.example.com")] +
                     [DnsRecord(ts=now - i, client_ip="192.168.0.5", qname=f"n{i}.example.com") for i in range(50)])
    await host.pipe.flush()
    n = await host.store.prune_dns(now - 7 * 86400, max_rows=20)
    assert n == 31
    left = await host.store.query_dns(limit=1000)
    names = {r["qname"] for r in left["queries"]}
    assert len(names) == 20 and "old.example.com" not in names     # the cap keeps the most recently written
