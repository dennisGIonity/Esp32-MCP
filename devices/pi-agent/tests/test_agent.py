"""
AEDI - IONITY GLOBAL | Pi agent 2 (EWP/1) against a fake Pi filesystem | Policy 986 AED
    cd devices/pi-agent && python -m pytest -q
"""
import socket
import time

import pytest

import ewp_cbor as cbor
import ewp_protocol as P
import ionity_agent as ia

K = P.KEYS


@pytest.fixture
def fakepi(tmp_path, monkeypatch):
    def w(rel, text):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    w("proc/cpuinfo", "processor\t: 0\nModel\t\t: Raspberry Pi Zero 2 W Rev 1.0\nSerial\t\t: 00000000a1b2c3d4\n")
    w("proc/device-tree/model", "Raspberry Pi Zero 2 W Rev 1.0\x00")
    w("proc/meminfo", "MemTotal:  438000 kB\nMemFree:  100000 kB\nMemAvailable:  219000 kB\n")
    w("sys/class/thermal/thermal_zone0/temp", "48312\n")
    w("proc/net/wireless", "Inter-| sta-|   Quality        |\n face | tus | link level noise |\n"
                          " wlan0: 0000   52.  -58.  -256        0      0      0      0      0        0\n")
    w("proc/stat", "cpu  100 0 100 800 0 0 0 0 0 0\n")
    w("proc/loadavg", "0.42 0.30 0.20 1/123 4567\n")
    w("proc/uptime", "3600.55 3000.00\n")
    w("sys/class/leds/ACT/brightness", "0")
    w("sys/class/leds/ACT/trigger", "none [mmc0] timer heartbeat")
    monkeypatch.setattr(ia, "FS", tmp_path)
    monkeypatch.setattr(ia.nob, "FS", tmp_path)
    monkeypatch.setattr(ia, "throttled", lambda: 0)
    monkeypatch.setattr(ia, "_cpu_prev", None)
    cfg = ia.load_config(None)
    cfg["state_file"] = str(tmp_path / "state.json")
    cfg["server"] = "127.0.0.1"
    return tmp_path, cfg


class FakeMqtt:
    def __init__(self):
        self.sent = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.sent.append((topic, bytes(payload), qos, retain))

        class R:
            rc = 0
        return R()

    def frames(self, ch):
        return [cbor.decode_frame(p) for t, p, _q, _r in self.sent if t.endswith("/" + ch)]


def online(agent):
    agent.mqtt, agent.mqtt_ok = FakeMqtt(), True
    return agent.mqtt


def req(tool, body=None, rid=0x0100002A, flags=0):
    return cbor.encode_frame(P.MSG_REQ, body or {}, rid=rid, tool=tool, flags=flags, deadline_ms=3000)


def wait_rs(m, n=1, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        rs = m.frames("rs")
        if len(rs) >= n:
            return rs
        time.sleep(0.02)
    raise AssertionError(f"expected {n} rs frames, got {m.frames('rs')}")


def test_identity_is_a_valid_hwid(fakepi):
    assert ia.device_id() == "pi-a1b2c3d4"
    assert ia.product() == "Raspberry Pi Zero 2 W Rev 1.0"


def test_hello_and_telemetry_follow_the_contract(fakepi):
    _, cfg = fakepi
    a = ia.Agent(cfg)
    h = cbor.named("HelloBody", cbor.loads(cbor.dumps(a.hello())))
    assert h["hwid"] == "pi-a1b2c3d4" and len(h["uuid"]) == 16 and h["proto"] == [1, 1]
    assert cbor.named("FirmwareInfo", h["fw"])["version"] == ia.FW_VERSION
    names = {P.TOOL_NAME[t[0]] for t in h["tools"]}
    assert {"get_device_info", "read_telemetry", "identify", "reboot", "set_actuator"} <= names
    assert "set_watchlist" not in names                      # DNS forwarder off by default
    assert "net" not in h
    ia.cpu_pct()
    tm = cbor.named("TelemetryBody", cbor.loads(cbor.dumps(a.telemetry_body())))
    heap = cbor.named("HeapStats", tm["heap"])
    assert heap["internalFree"] == 219000 * 1024
    assert cbor.named("NetStats", tm["net"])["rssiDbm"] == -58
    assert cbor.named("SensorStats", tm["sensors"])["dieTempC"] == pytest.approx(48.3)
    assert tm["ext"][1] == 50.0                                # mem_used_pct by HELLO metric id
    assert a.topic("tm") == "ionity/ewp1/lab/pi-a1b2c3d4/tm"


def test_uuid_and_boot_count_persist(fakepi):
    _, cfg = fakepi
    a1 = ia.Agent(cfg)
    a2 = ia.Agent(cfg)
    assert a1.uuid == a2.uuid and a2.state["boot_count"] == a1.state["boot_count"] + 1


def test_tool_calls_answer_with_res_and_err(fakepi):
    _, cfg = fakepi
    a = ia.Agent(cfg)
    m = online(a)
    a.on_downlink(req(P.TOOL_READ_TELEMETRY, {1: P.TM_HEAP}, flags=P.FLAG_ACK_REQUESTED))
    rs = wait_rs(m, 2)
    assert rs[0][0]["type"] == P.MSG_ACK and rs[0][0]["status"] == P.ST_ACCEPTED
    hdr, body = rs[1]
    assert hdr["type"] == P.MSG_RES and hdr["rid"] == 0x0100002A and hdr["tool"] == P.TOOL_READ_TELEMETRY
    res = cbor.named("ResultBody", body)
    assert "heap" in cbor.named("TelemetryBody", res["result"])
    assert cbor.named("ExecTiming", res["timing"])["execUs"] >= 0
    # the same rid again is a duplicate, an unknown tool is refused, a bad frame is answered
    a.on_downlink(req(P.TOOL_READ_TELEMETRY))
    a.on_downlink(req(P.TOOL_RUN_INFERENCE, rid=2))
    a.on_downlink(b"\xe5\x01garbage")
    rs = wait_rs(m, 5)
    assert [h["status"] for h, _ in rs[2:]] == [P.ST_DUPLICATE, P.ST_UNKNOWN_TOOL, P.ST_BAD_FRAME]


def test_set_actuator_drives_the_alert_led_and_lease(fakepi):
    root, cfg = fakepi
    a = ia.Agent(cfg)
    m = online(a)
    a.on_downlink(req(P.TOOL_SET_ACTUATOR, {1: 0, 2: 1, 4: 200}, rid=10))
    hdr, body = wait_rs(m)[0]
    assert hdr["type"] == P.MSG_RES, body
    assert (root / "sys/class/leds/ACT/brightness").read_text() == "1"
    assert (root / "sys/class/leds/ACT/trigger").read_text() == "none"
    time.sleep(0.25)
    a.housekeeping()
    assert (root / "sys/class/leds/ACT/brightness").read_text() == "0"
    assert (root / "sys/class/leds/ACT/trigger").read_text() == "mmc0"     # handed back to the kernel
    ev = [cbor.named("EventBody", b) for _h, b in m.frames("ev")]
    assert ev[-1]["kind"] == P.EVENT_LEASE_EXPIRED
    a.on_downlink(req(P.TOOL_SET_ACTUATOR, {1: 0, 2: 0.5}, rid=11))
    assert wait_rs(m, 2)[1][0]["status"] == P.ST_INVALID_ARGS


@pytest.fixture
def dnsagent(fakepi, monkeypatch):
    _, cfg = fakepi
    cfg.update(dns_mode="forward", dns_port="0", dns_bind="127.0.0.1", dns_upstream="127.0.0.1")
    a = ia.Agent(cfg)
    a.dns.want_tcp = False
    a.dns.want_discovery = False
    assert a.dns.start()
    yield a
    a.dns.stop()


def test_dns_tools_and_flag_events(dnsagent, fakepi):
    root, _ = fakepi
    a = dnsagent
    m = online(a)
    h = cbor.named("HelloBody", a.hello())
    net = cbor.named("NetObserveInfo", h["net"])
    assert net["mode"] == P.DNS_MODE_FORWARD and net["watchVer"] == P.DEFAULT_WATCHLIST_VERSION
    assert net["caps"] & P.NETCAP_DNS_FORWARD
    # a query from a LAN client hits the watchlist: DNS_FLAGGED at once + alert LED
    a.dns._record("192.168.0.42", "www.youtube.com", 1, 0, 12.0, 0)
    ev = [cbor.named("EventBody", b) for _h, b in m.frames("ev")]
    assert ev[0]["kind"] == P.EVENT_DNS_FLAGGED
    f = cbor.named("DnsFlag", ev[0]["flag"])
    assert f["qname"] == "www.youtube.com" and f["rule"] == "youtube.com" and f["category"] == 1
    assert f["client"][0] == socket.inet_aton("192.168.0.42")
    assert (root / "sys/class/leds/ACT/brightness").read_text() == "1"
    # the periodic batch carries both
    a.dns._record("192.168.0.43", "example.com", 28, 0, 4.0, 0)
    assert a.flush_dns() == 1
    batch = cbor.named("DnsBatch", cbor.named("EventBody", m.frames("ev")[-1][1])["dns"])
    assert {q[2] for q in batch["queries"]} == {"www.youtube.com", "example.com"}
    assert {socket.inet_ntoa(c[0]) for c in batch["clients"]} == {"192.168.0.42", "192.168.0.43"}
    # watchlist from the host, then read back
    a.on_downlink(req(P.TOOL_SET_WATCHLIST, {1: P.WATCH_OP_REPLACE, 2: 77, 3: [["netflix.com", 4, 1]],
                                             4: [[4, "video_streaming:netflix"]]}, rid=20))
    a.on_downlink(req(P.TOOL_GET_WATCHLIST, rid=21))
    rs = {h["rid"]: cbor.named("ResultBody", b) for h, b in wait_rs(m, 2)}
    assert cbor.named("WatchlistResult", rs[20]["result"]) == {"version": 77, "rules": 1, "categories": 1}
    assert cbor.named("GetWatchlistResult", rs[21]["result"])["rules"] == [["netflix.com", 4, 1]]
    assert a.state["watchlist"]["version"] == 77
    for tool, rid in ((P.TOOL_GET_NETWORK_STATS, 30), (P.TOOL_GET_LOCAL_DEVICES, 31), (P.TOOL_QUERY_DNS_LOG, 32),
                      (P.TOOL_FLUSH_DNS_CACHE, 33)):
        a.on_downlink(req(tool, rid=rid))
    rs = {h["rid"]: (h, cbor.named("ResultBody", b)) for h, b in wait_rs(m, 6)}
    assert all(rs[r][0]["type"] == P.MSG_RES for r in (30, 31, 32, 33))
    devs = cbor.named("LocalDevicesResult", rs[31][1]["result"])
    assert devs["total"] == 2
    log_ = cbor.named("DnsLogResult", rs[32][1]["result"])
    assert log_["queries"][0][2] == "example.com"               # newest first
