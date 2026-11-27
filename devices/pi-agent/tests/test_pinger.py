"""
AEDI - IONITY GLOBAL | Pi agent 2.1 ping_host (0x47) against a fake ICMP socket | Policy 986 AED
    cd devices/pi-agent && python -m pytest -q
"""
import socket
import struct

import pytest

import ewp_cbor as cbor
import ewp_protocol as P
import ionity_agent as ia
import pinger
from test_agent import fakepi, online, req, wait_rs  # noqa: F401  (fixture + helpers)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeIcmp:
    """Answers echo i after rtts[i] ms (None = lost). raw=True prepends a 20-byte IP header."""

    def __init__(self, clock, rtts, raw=False, ident_override=None):
        self.clock, self.rtts, self.raw, self.ident_override = clock, list(rtts), raw, ident_override
        self.sent, self.inbox, self.timeout, self.closed = [], [], None, False

    def sendto(self, pkt, addr):
        assert pinger.checksum(pkt) == 0           # a valid ICMP checksum sums to zero
        t, _c, _k, ident, seq = struct.unpack("!BBHHH", pkt[:8])
        assert t == pinger.ICMP_ECHO_REQUEST
        i = len(self.sent)
        self.sent.append((pkt, addr))
        rtt = self.rtts[i % len(self.rtts)]
        if rtt is not None:
            # a stray reply from another host first: must be ignored
            self.inbox.append((self.clock.t, self._reply(ident, seq), ("10.9.9.9", 0)))
            self.inbox.append((self.clock.t + rtt / 1000.0, self._reply(ident, seq), addr))

    def _reply(self, ident, seq):
        body = struct.pack("!BBHHH", pinger.ICMP_ECHO_REPLY, 0, 0, self.ident_override or ident, seq)
        return (b"\x45" + b"\0" * 19 + body) if self.raw else body

    def settimeout(self, t):
        self.timeout = t

    def recvfrom(self, _n):
        if self.inbox and self.inbox[0][0] <= self.clock.t + self.timeout:
            at, pkt, frm = self.inbox.pop(0)
            self.clock.t = max(self.clock.t, at)
            return pkt, frm
        self.clock.t += self.timeout
        self.inbox = [x for x in self.inbox if x[0] > self.clock.t]
        raise socket.timeout()

    def close(self):
        self.closed = True


def make(clock, sock=None, refuse=(), resolve=None):
    calls = []

    def factory(fam, kind, proto):
        calls.append(kind)
        if kind in refuse:
            raise PermissionError(1, "Operation not permitted")
        return sock

    p = pinger.Pinger(sock_factory=factory, resolve=resolve or (lambda h: {"nas.lan": "192.168.0.7"}.get(h, h)),
                      clock=clock, sleep=clock.sleep)
    return p, calls


def test_unprivileged_socket_all_answered():
    c = Clock()
    s = FakeIcmp(c, [12, 9, 15, 11])
    p, calls = make(c, s)
    r = p.ping("nas.lan", 4, 1000, 1000)
    assert calls == [socket.SOCK_DGRAM]
    assert r["error"] == P.PING_NONE and r["sent"] == 4 and r["received"] == 4
    assert r["resolvedIp"] == socket.inet_aton("192.168.0.7")
    assert (r["rttMinMs"], r["rttAvgMs"], r["rttMaxMs"]) == (9, 12, 15)
    assert s.closed


def test_raw_fallback_partial_loss_and_id_check():
    c = Clock()
    s = FakeIcmp(c, [20, None, None, 30], raw=True)
    p, calls = make(c, s, refuse=(socket.SOCK_DGRAM,))
    r = p.ping("192.168.0.1", 4, 200, 200)
    assert calls == [socket.SOCK_DGRAM, socket.SOCK_RAW]
    assert r["error"] == P.PING_NONE and r["received"] == 2 and r["rttAvgMs"] == 25
    # on a raw socket a reply with someone else's echo id is not ours
    c2 = Clock()
    p2, _ = make(c2, FakeIcmp(c2, [5], raw=True, ident_override=0xBEEF), refuse=(socket.SOCK_DGRAM,))
    assert p2.ping("192.168.0.1", 1, 100, 200)["error"] == P.PING_TIMEOUT


def test_timeout_resolve_and_not_permitted():
    c = Clock()
    p, _ = make(c, FakeIcmp(c, [None]))
    r = p.ping("192.168.0.99", 2, 100, 200)
    assert r["error"] == P.PING_TIMEOUT and r["sent"] == 2 and r["received"] == 0 and "rttAvgMs" not in r

    def nx(_h):
        raise socket.gaierror(-2, "Name or service not known")
    p, _ = make(c, FakeIcmp(c, [1]), resolve=nx)
    assert p.ping("nowhere.example", 1, 100, 200) == {"error": P.PING_RESOLVE_FAILED, "sent": 0, "received": 0}

    p, calls = make(c, None, refuse=(socket.SOCK_DGRAM, socket.SOCK_RAW))
    r = p.ping("192.168.0.1", 1, 100, 200)
    assert r["error"] == P.PING_NOT_PERMITTED and r["sent"] == 0


def test_one_session_at_a_time():
    c = Clock()
    p, _ = make(c, FakeIcmp(c, [1]))
    assert p._busy.acquire(blocking=False)
    try:
        assert p.ping("192.168.0.1", 1, 100, 200)["error"] == P.PING_BUSY
    finally:
        p._busy.release()


@pytest.mark.parametrize("t,ok", [("example.com", True), ("192.168.0.1", True), ("a-b.c", True),
                                  ("", False), ("bad host", False), ("-x.com", False), ("x;reboot", False),
                                  ("a" * 254, False)])
def test_target_validation(t, ok):
    assert pinger.valid_target(t) is ok


def test_agent_ping_host_tool(fakepi, monkeypatch):  # noqa: F811
    _, cfg = fakepi
    a = ia.Agent(cfg)
    assert [P.TOOL_PING_HOST, 1, P.TOOL_FLAG_READ_ONLY | P.TOOL_FLAG_LONG_RUNNING] in a.tools()
    c = Clock()
    a.pinger, _ = make(c, FakeIcmp(c, [7, 9]))
    m = online(a)
    kp = P.KEYS["PingHostArgs"]
    a.on_downlink(req(P.TOOL_PING_HOST, {kp["target"]: "nas.lan", kp["count"]: 2, kp["timeoutMs"]: 100,
                                         kp["intervalMs"]: 200}, rid=50))
    a.on_downlink(req(P.TOOL_PING_HOST, {kp["target"]: "x;reboot"}, rid=51))
    a.on_downlink(req(P.TOOL_PING_HOST, {kp["target"]: "nas.lan", kp["count"]: 11}, rid=52))
    rs = {h["rid"]: (h, b) for h, b in wait_rs(m, 3)}
    h, b = rs[50]
    assert h["type"] == P.MSG_RES
    r = cbor.named("PingHostResult", cbor.named("ResultBody", b)["result"])
    assert r["error"] == P.PING_NONE and r["received"] == 2 and r["rttMinMs"] == 7
    for rid, key in ((51, kp["target"]), (52, kp["count"])):
        h, b = rs[rid]
        assert h["type"] == P.MSG_ERR and h["status"] == P.ST_INVALID_ARGS
        assert cbor.named("ErrorBody", b)["argKey"] == key
