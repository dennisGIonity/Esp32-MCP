"""
AEDI - IONITY GLOBAL | Pi agent NetObserve - real sockets on loopback | Policy 986 AED
    cd devices/pi-agent && python -m pytest -q
A fake upstream resolver answers on 127.0.0.x; the forwarder runs on an
ephemeral port exactly as it does on :53 in the field.
"""
import socket
import struct
import threading
import time

import pytest

import ewp_cbor as cbor
import ewp_protocol as P
import netobserve as nob


def build_query(name: str, qtype: int = 1, qid: int = 0x1234) -> bytes:
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    for label in name.split("."):
        q += bytes([len(label)]) + label.encode()
    return q + b"\x00" + struct.pack(">HH", qtype, 1)


def answer_for(query: bytes, ip: str = "203.0.113.7", ttl: int = 120, rcode: int = 0) -> bytes:
    qd_end = 12
    while query[qd_end]:
        qd_end += query[qd_end] + 1
    qd_end += 5
    hdr = query[:2] + struct.pack(">HHHHH", 0x8180 | rcode, 1, 0 if rcode else 1, 0, 0)
    body = query[12:qd_end]
    if not rcode:
        body += b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, ttl, 4) + socket.inet_aton(ip)
    return hdr + body


class FakeUpstream:
    def __init__(self, host="127.0.0.1", silent=False):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind((host, 0))
        self.s.settimeout(0.2)
        self.port = self.s.getsockname()[1]
        self.silent = silent
        self.seen = []
        self.stop = threading.Event()
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while not self.stop.is_set():
            try:
                data, addr = self.s.recvfrom(4096)
            except OSError:
                continue
            self.seen.append(data)
            if not self.silent:
                self.s.sendto(answer_for(data), addr)


@pytest.fixture
def fwd(monkeypatch):
    up = FakeUpstream()
    flags = []
    n = nob.NetObserve(upstreams=["127.0.0.1"], port=0, bind="127.0.0.1", on_flag=flags.append,
                       tcp=False, discovery=False)
    # the fake upstream listens on an ephemeral port, not 53
    orig = n._send_upstream

    def send(new_id, p):
        p.tries += 1
        p.sent = time.monotonic()
        n._up.sendto(struct.pack(">H", new_id) + p.query[2:], ("127.0.0.1", up.port))
    monkeypatch.setattr(n, "_send_upstream", send)
    assert orig
    assert n.start()
    yield n, up, flags
    n.stop()
    up.stop.set()


def ask(port: int, name: str, qtype: int = 1, qid: int = 0x4242, timeout: float = 4.0) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as c:
        c.settimeout(timeout)
        c.sendto(build_query(name, qtype, qid), ("127.0.0.1", port))
        return c.recvfrom(4096)[0]


def test_forwards_logs_and_caches(fwd):
    n, up, _ = fwd
    r = ask(n.port, "Example.ORG", qid=0x0101)
    assert r[:2] == b"\x01\x01" and r[3] & 0xF == 0 and r.endswith(socket.inet_aton("203.0.113.7"))
    assert len(up.seen) == 1 and up.seen[0][:2] != b"\x01\x01"          # fresh upstream id, never the client's
    r2 = ask(n.port, "example.org", qid=0x0202)                         # case-insensitive cache hit
    assert r2[:2] == b"\x02\x02" and len(up.seen) == 1
    assert b"\x07example\x03org" in r2                                   # the client's own question bytes
    c = n.counters()
    assert c["queries"] == 2 and c["forwarded"] == 1 and c["cacheHits"] == 1
    batch, dropped = n.take_batch()
    assert [b["qname"] for b in batch] == ["example.org", "example.org"] and dropped == 0
    assert batch[1]["flags"] & P.DNSQ_CACHED
    assert n.flush_cache() == 1


def test_watchlist_flags_youtube_once_per_window(fwd):
    n, _up, flags = fwd
    ask(n.port, "www.youtube.com")
    ask(n.port, "rr3---sn-abc.googlevideo.com")
    ask(n.port, "googlevideo.com", qid=7)                    # *.googlevideo.com: bare domain does not match
    ask(n.port, "www.youtube.com", qid=8)                    # same client + rule inside 30 s: no second event
    assert [f["rule"] for f in flags] == ["youtube.com", "googlevideo.com"]
    assert all(f["category"] == 1 for f in flags)
    batch, _ = n.take_batch()
    flagged = {b["qname"]: bool(b["flags"] & P.DNSQ_FLAGGED) for b in batch}
    assert flagged == {"www.youtube.com": True, "rr3---sn-abc.googlevideo.com": True, "googlevideo.com": False}


def test_servfail_when_upstream_silent(monkeypatch):
    up = FakeUpstream(silent=True)
    n = nob.NetObserve(upstreams=["127.0.0.1"], port=0, bind="127.0.0.1", tcp=False, discovery=False)

    def send(new_id, p):
        p.tries += 1
        p.sent = time.monotonic()
        n._up.sendto(struct.pack(">H", new_id) + p.query[2:], ("127.0.0.1", up.port))
    monkeypatch.setattr(n, "_send_upstream", send)
    assert n.start()
    try:
        t0 = time.monotonic()
        r = ask(n.port, "slow.example", qid=0x0909)
        assert r[:2] == b"\x09\x09" and r[3] & 0xF == 2
        assert 2.5 <= time.monotonic() - t0 < 4.5
        assert len(up.seen) == 2                               # retried once after 1.5 s
        assert n.counters()["upstreamFail"] == 1
    finally:
        n.stop()
        up.stop.set()


def test_refuses_public_clients_and_garbage():
    n = nob.NetObserve(upstreams=["127.0.0.1"], port=0, bind="127.0.0.1", tcp=False, discovery=False)
    assert n.client_allowed("192.168.0.42") and n.client_allowed("10.1.2.3") and n.client_allowed("127.0.0.1")
    assert not n.client_allowed("8.8.8.8") and not n.client_allowed("not-an-ip")
    with pytest.raises(nob.DnsParseError):
        nob.parse_query(b"\x00" * 5)
    with pytest.raises(nob.DnsParseError):                    # a pointer loop
        nob.read_name(b"\x00" * 12 + b"\xc0\x0c", 12)


def test_watchlist_rules_and_versions():
    w = nob.Watchlist()
    assert w.version == P.DEFAULT_WATCHLIST_VERSION
    assert w.match("m.youtube.com")[0] == "youtube.com"
    assert w.match("notyoutube.com") is None
    assert w.match("i.ytimg.com")[1] == 1 and w.match("ytimg.com") is None
    assert w.apply(P.WATCH_OP_REPLACE, 9, [["example.com", 3, P.RULE_ALERT]], [[3, "custom:x"]]) == (9, 1, 1)
    assert w.match("a.b.example.com") == ("example.com", 3, P.RULE_ALERT)
    assert w.match("youtube.com") is None
    with pytest.raises(ValueError):
        w.apply(P.WATCH_OP_APPEND, 10, [["x.org", 3, 0]], None)          # APPEND must keep the version
    w.apply(P.WATCH_OP_APPEND, 9, [["x.org", 3, 0]], None)
    with pytest.raises(ValueError):
        w.apply(P.WATCH_OP_REPLACE, 11, [["bad suffix!", 1, 0]], None)  # validated before anything changes
    assert w.version == 9 and len(w.rules) == 2
    snap = w.snapshot()
    w2 = nob.Watchlist()
    w2.restore(snap)
    assert w2.snapshot() == snap


def test_batch_folding_and_frame_split():
    n = nob.NetObserve(upstreams=["127.0.0.1"], port=0, tcp=False, discovery=False)
    now = time.monotonic()
    batch = []
    for i in range(300):
        ip = f"192.168.0.{10 + i % 40}"
        n.clients.touch(ip, mac=bytes([2, 0, 0, 0, 0, i % 40]), hostname=f"host{i % 40}")
        batch.append({"t": now - 1 + i / 1000, "ip": ip, "qname": f"svc{i % 120}.example.com", "qtype": 1,
                      "rcode": 0, "latency": 5, "flags": 0, "category": 0})
    chunks = n.batch_frames(batch, now=now, max_body=1500)
    assert len(chunks) > 1
    seen = 0
    for clients, queries in chunks:
        body = cbor.keyed("DnsBatch", {"seq": 1, "windowMs": 10_000, "clients": clients, "queries": queries})
        assert len(cbor.dumps(body)) <= 1500 + 64
        for q in queries:
            assert 0 <= q[1] < len(clients)                   # every index points into THIS frame's clients
            seen += q[6]
    assert seen == 300                                         # folded counts add up to every query


def test_dhcp_and_mdns_parsers():
    mac = bytes.fromhex("3c22fb010203")
    pkt = bytearray(300)
    pkt[0:3] = b"\x01\x01\x06"
    pkt[28:34] = mac
    pkt[236:240] = b"\x63\x82\x53\x63"
    opts = b"\x35\x01\x03" + b"\x0c\x0akitchen-tv" + b"\x32\x04" + socket.inet_aton("192.168.0.55") + b"\xff"
    pkt[240:240 + len(opts)] = opts
    assert nob.parse_dhcp(bytes(pkt)) == (mac, "kitchen-tv", "192.168.0.55")
    resp = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0) + b"\x0bliving-room\x05local\x00" + \
        struct.pack(">HHIH", 1, 0x8001, 120, 4) + socket.inet_aton("192.168.0.60")
    assert nob.parse_mdns(resp) == [("192.168.0.60", "living-room")]


def test_cbor_codec_against_reference():
    cbor2 = pytest.importorskip("cbor2")
    samples = [0, 23, 24, 255, 256, 65535, 65536, 2**32, -1, -500, 0.5, 1.1, "héllo", b"\x00\x01",
               [1, [2, [3]]], {1: "a", 2: [b"\xff", True, False, None]}]
    for v in samples:
        assert cbor.loads(cbor.dumps(v)) == v
        assert cbor2.loads(cbor.dumps(v)) == v
        assert cbor.loads(cbor2.dumps(v)) == v
    with pytest.raises(cbor.CborError):
        cbor.loads(b"\x9f\x01\xff")                            # indefinite length
    with pytest.raises(cbor.CborError):
        cbor.dumps(float("nan"))
    with pytest.raises(cbor.CborError):
        cbor.loads(b"\xa2\x01\x01\x01\x02")                    # duplicate key
    f = cbor.encode_frame(P.MSG_EVENT, {1: 6}, rid=5)
    hdr, body = cbor.decode_frame(f)
    assert hdr["type"] == P.MSG_EVENT and hdr["rid"] == 5 and body == {1: 6}
    with pytest.raises(cbor.FrameError):
        cbor.decode_frame(f[:-1])
