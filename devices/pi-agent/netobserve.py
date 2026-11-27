"""
AEDI - IONITY GLOBAL | NetObserve for the Raspberry Pi agent
Doc ID: DOC-2026-10-ESP32MCP-PIAGENT2 | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

The Pi as the LAN's DNS forwarder - the same job, the same rules and the same
wire contract as the ESP32 firmware's NetObserve component
(firmware/esp32-node/components/netobserve, packages/protocol/src/netobserve.ts):

  * UDP *and* TCP port 53, forwarded to the configured upstreams (never to the
    DHCP-learned resolver: on a LAN whose router hands out THIS Pi as DNS, that
    would be a loop). Non-blocking: one selector thread owns every UDP socket;
    a slow upstream never delays another client.
  * Who asked: client IPv4 -> MAC from the kernel neighbour table
    (/proc/net/arp), host names from DHCP broadcasts (option 12) and mDNS.
  * What: QNAME, QTYPE, RCODE, latency, cache hit - metadata only, never answers.
  * Watchlist: label-boundary suffix rules with categories; ALERT rules publish
    DNS_FLAGGED immediately (once per client+rule per 30 s) and light the alert LED.
  * Batches of DnsQuery tuples every flush interval (DNS_BATCH events).

Only clients on private / link-local / loopback networks are answered, so the
Pi can never become an open resolver.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import random
import re
import selectors
import socket
import socketserver
import struct
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Callable

import ewp_protocol as P

log = logging.getLogger("ionity-agent.netobserve")

DEFAULT_UPSTREAMS = ("1.1.1.1", "8.8.8.8")
RETRY_AFTER_S = 1.5
GIVE_UP_AFTER_S = 3.0
FLAG_REPEAT_S = 30.0
CACHE_MAX = 512
TTL_MIN, TTL_MAX = 15, 3600
LOG_MAX = 2048
PENDING_MAX = 512

FS = Path(os.environ.get("IONITY_AGENT_FSROOT", "/"))


# ---------------------------------------------------------------------------
# DNS wire helpers
# ---------------------------------------------------------------------------
class DnsParseError(ValueError):
    pass


def read_name(pkt: bytes, off: int, max_hops: int = 32) -> tuple[str, int]:
    """(lower-case presentation name, offset after the name in the ORIGINAL position)."""
    labels: list[str] = []
    end = -1
    hops = 0
    total = 0
    while True:
        if off >= len(pkt):
            raise DnsParseError("name runs past the packet")
        ln = pkt[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            if off + 1 >= len(pkt):
                raise DnsParseError("truncated pointer")
            if end < 0:
                end = off + 2
            hops += 1
            if hops > max_hops:
                raise DnsParseError("pointer loop")
            off = ((ln & 0x3F) << 8) | pkt[off + 1]
            continue
        if ln & 0xC0:
            raise DnsParseError("reserved label type")
        off += 1
        if off + ln > len(pkt):
            raise DnsParseError("label runs past the packet")
        total += ln + 1
        if total > 255:
            raise DnsParseError("name longer than 255 bytes")
        labels.append(pkt[off:off + ln].decode("ascii", "replace").lower())
        off += ln
    return ".".join(labels), (end if end >= 0 else off)


def parse_query(pkt: bytes) -> tuple[str, int, int, int]:
    """A client query -> (qname, qtype, qclass, end of question section)."""
    if len(pkt) < 12:
        raise DnsParseError("shorter than a DNS header")
    flags, qd = struct.unpack_from(">HH", pkt, 2)
    if flags & 0x8000:
        raise DnsParseError("not a query")
    if (flags >> 11) & 0xF != 0:
        raise DnsParseError("only standard queries are forwarded")
    if qd != 1:
        raise DnsParseError("exactly one question expected")
    name, off = read_name(pkt, 12)
    if off + 4 > len(pkt):
        raise DnsParseError("truncated question")
    qtype, qclass = struct.unpack_from(">HH", pkt, off)
    return name, qtype, qclass, off + 4


def response_info(pkt: bytes) -> tuple[int, int, bool, int]:
    """(rcode, answer count, truncated, smallest answer TTL or -1)."""
    if len(pkt) < 12:
        raise DnsParseError("short response")
    flags, qd, an = struct.unpack_from(">HHH", pkt, 2)
    rcode, tc = flags & 0xF, bool(flags & 0x0200)
    off = 12
    for _ in range(qd):
        _, off = read_name(pkt, off)
        off += 4
    ttl = -1
    for _ in range(an):
        _, off = read_name(pkt, off)
        if off + 10 > len(pkt):
            raise DnsParseError("truncated answer")
        _t, _c, t, rdlen = struct.unpack_from(">HHIH", pkt, off)
        off += 10 + rdlen
        ttl = t if ttl < 0 else min(ttl, t)
    return rcode, an, tc, ttl


def servfail_for(query: bytes, q_end: int) -> bytes:
    """SERVFAIL echoing the client's id and question (no answer, no EDNS)."""
    hdr = bytearray(query[:12])
    hdr[2] = 0x80 | (query[2] & 0x01)            # QR, keep RD
    hdr[3] = 0x80 | 0x02                         # RA, RCODE=2
    struct.pack_into(">HHHH", hdr, 4, 1, 0, 0, 0)
    return bytes(hdr) + query[12:q_end]


# ---------------------------------------------------------------------------
# Watchlist - label-boundary suffix matching (same rules as Watchlist.cpp)
# ---------------------------------------------------------------------------
def pattern_to_rule(pattern: str) -> tuple[str, int]:
    p = pattern.strip().lower().rstrip(".")
    if p.startswith("*."):
        return p[2:], P.RULE_SUBDOMAINS_ONLY
    return p, 0


_SUFFIX_RE = re.compile(r"^(?=.{1,96}$)[a-z0-9_]([a-z0-9_-]*[a-z0-9_])?(\.[a-z0-9_]([a-z0-9_-]*[a-z0-9_])?)*$")


class Watchlist:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.version = 0
        self.rules: dict[str, tuple[int, int]] = {}
        self.categories: dict[int, str] = {}
        self.load_defaults()

    def load_defaults(self) -> None:
        with self.lock:
            self.version = P.DEFAULT_WATCHLIST_VERSION
            self.categories = {int(c["id"]): str(c["name"]) for c in P.DEFAULT_CATEGORIES}
            self.rules = {}
            for r in P.DEFAULT_RULES:
                suffix, sub = pattern_to_rule(r["pattern"])
                self.rules[suffix] = (int(r["category"]), int(r["flags"]) | sub)

    @staticmethod
    def check_rule(rule: Any) -> tuple[str, int, int]:
        if not isinstance(rule, (list, tuple)) or len(rule) != 3:
            raise ValueError("a rule is [suffix, category, flags]")
        suffix, cat, flags = rule
        if not isinstance(suffix, str) or not _SUFFIX_RE.match(suffix.lower()):
            raise ValueError(f"bad suffix {suffix!r}")
        if not isinstance(cat, int) or not 0 <= cat <= P.LIMITS["watchCategories"]:
            raise ValueError("category out of range")
        if not isinstance(flags, int) or flags & ~P.RULE_FLAGS_KNOWN:
            raise ValueError("unknown rule flags")
        return suffix.lower(), cat, flags

    def apply(self, op: int, version: int, rules: list, categories: list | None) -> tuple[int, int, int]:
        """SET_WATCHLIST. Validates everything before changing anything."""
        if op not in (P.WATCH_OP_REPLACE, P.WATCH_OP_APPEND):
            raise ValueError("op must be REPLACE or APPEND")
        if not isinstance(version, int) or not 0 <= version <= 0xFFFFFFFF:
            raise ValueError("version must be a u32")
        checked = [self.check_rule(r) for r in (rules or [])]
        cats: dict[int, str] = {}
        for c in categories or []:
            if not isinstance(c, (list, tuple)) or len(c) != 2 or not isinstance(c[0], int) \
                    or not 1 <= c[0] <= P.LIMITS["watchCategories"] or not isinstance(c[1], str):
                raise ValueError("a category is [id 1..32, name]")
            cats[c[0]] = c[1][:48]
        with self.lock:
            if op == P.WATCH_OP_APPEND and version != self.version:
                raise ValueError("APPEND must carry the version of the preceding REPLACE")
            new_rules = {} if op == P.WATCH_OP_REPLACE else dict(self.rules)
            for s, cat, fl in checked:
                new_rules[s] = (cat, fl)
            if len(new_rules) > P.LIMITS["watchRules"]:
                raise OverflowError(f"more than {P.LIMITS['watchRules']} rules")
            new_cats = {} if op == P.WATCH_OP_REPLACE else dict(self.categories)
            new_cats.update(cats)
            self.rules, self.categories, self.version = new_rules, new_cats, version
            return self.version, len(self.rules), len(self.categories)

    def match(self, qname: str) -> tuple[str, int, int] | None:
        """-> (suffix, category, flags) of the most specific rule, or None."""
        labels = qname.lower().rstrip(".").split(".")
        with self.lock:
            for i in range(len(labels)):
                cand = ".".join(labels[i:])
                hit = self.rules.get(cand)
                if hit is None:
                    continue
                cat, flags = hit
                if i == 0 and flags & P.RULE_SUBDOMAINS_ONLY:
                    continue
                return cand, cat, flags
        return None

    def snapshot(self) -> dict:
        with self.lock:
            return {"version": self.version,
                    "rules": [[s, c, f] for s, (c, f) in sorted(self.rules.items())],
                    "categories": [[i, n] for i, n in sorted(self.categories.items())]}

    def restore(self, snap: dict) -> None:
        try:
            self.apply(P.WATCH_OP_REPLACE, int(snap["version"]), snap["rules"], snap.get("categories"))
        except (KeyError, TypeError, ValueError, OverflowError) as e:
            log.warning("stored watchlist ignored (%s) - using the built-in list", e)
            self.load_defaults()


# ---------------------------------------------------------------------------
# Local-device table
# ---------------------------------------------------------------------------
_ARP_RE = re.compile(r"^(\d+\.\d+\.\d+\.\d+)\s+0x\w+\s+0x(\w+)\s+([0-9a-fA-F:]{17})\s+\S+\s+(\S+)")


def mac_bytes(mac: str | None) -> bytes:
    if not mac:
        return b""
    try:
        b = bytes(int(x, 16) for x in mac.split(":"))
    except ValueError:
        return b""
    return b if len(b) == 6 and b != b"\x00" * 6 else b""


class ClientTable:
    def __init__(self, cap: int = P.LIMITS["localDevices"]):
        self.cap = cap
        self.lock = threading.Lock()
        self.t: OrderedDict[str, dict] = OrderedDict()

    def touch(self, ip: str, *, mac: bytes = b"", hostname: str = "", source: int = 0, query: bool = False) -> dict:
        now = time.monotonic()
        with self.lock:
            e = self.t.get(ip)
            if e is None:
                if len(self.t) >= self.cap:
                    self.t.popitem(last=False)         # least recently seen goes
                e = {"ip": ip, "mac": b"", "hostname": "", "seen": now, "queries": 0, "sources": 0}
                self.t[ip] = e
            self.t.move_to_end(ip)
            e["seen"] = now
            if mac:
                e["mac"] = mac
            if hostname:
                e["hostname"] = hostname[:63]
            e["sources"] |= source
            if query:
                e["queries"] += 1
            return dict(e)

    def by_mac(self, mac: bytes, hostname: str) -> None:
        with self.lock:
            for e in self.t.values():
                if e["mac"] == mac:
                    e["hostname"] = hostname[:63]
                    e["sources"] |= P.DISC_DHCP

    def get(self, ip: str) -> dict | None:
        with self.lock:
            e = self.t.get(ip)
            return dict(e) if e else None

    def refresh_arp(self) -> int:
        """Kernel neighbour table -> MACs (no root, no subprocess)."""
        n = 0
        try:
            text = (FS / "proc/net/arp").read_text(encoding="ascii", errors="replace")
        except OSError:
            return 0
        for line in text.splitlines()[1:]:
            m = _ARP_RE.match(line)
            if not m or int(m.group(2), 16) & 0x2 == 0:      # ATF_COM: entry complete
                continue
            mb = mac_bytes(m.group(3))
            if mb:
                self.touch(m.group(1), mac=mb, source=P.DISC_ARP)
                n += 1
        return n

    def rows(self, limit: int) -> tuple[list, int]:
        now = time.monotonic()
        with self.lock:
            items = list(reversed(self.t.values()))
            total = len(items)
        return [[socket.inet_aton(e["ip"]), e["mac"], e["hostname"], int(now - e["seen"]),
                 e["queries"], e["sources"]] for e in items[:limit]], total

    def recent_clients(self, window_s: float = 600.0) -> int:
        now = time.monotonic()
        with self.lock:
            return sum(1 for e in self.t.values() if e["queries"] and now - e["seen"] <= window_s)


# ---------------------------------------------------------------------------
# Passive discovery: DHCP option 12 and mDNS answers
# ---------------------------------------------------------------------------
def parse_dhcp(pkt: bytes) -> tuple[bytes, str, str] | None:
    """BOOTREQUEST -> (chaddr MAC, host name, requested IP or ''). None if not useful."""
    if len(pkt) < 240 or pkt[0] != 1 or pkt[1] != 1 or pkt[2] != 6:
        return None
    if pkt[236:240] != b"\x63\x82\x53\x63":
        return None
    mac = bytes(pkt[28:34])
    host, req_ip = "", ""
    ciaddr = socket.inet_ntoa(pkt[12:16])
    i = 240
    while i < len(pkt):
        code = pkt[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        if i + 1 >= len(pkt):
            break
        ln = pkt[i + 1]
        val = pkt[i + 2:i + 2 + ln]
        if len(val) < ln:
            break
        if code == 12:
            host = val.decode("utf-8", "replace").strip("\x00 ")[:63]
        elif code == 50 and ln == 4:
            req_ip = socket.inet_ntoa(val)
        i += 2 + ln
    if ciaddr != "0.0.0.0":
        req_ip = req_ip or ciaddr
    if not host or not any(mac):
        return None
    return mac, host, req_ip


def parse_mdns(pkt: bytes) -> list[tuple[str, str]]:
    """mDNS response -> [(ipv4, host name without .local)] from A records."""
    out: list[tuple[str, str]] = []
    if len(pkt) < 12:
        return out
    flags, qd, an, ns, ar = struct.unpack_from(">HHHHH", pkt, 2)
    if not flags & 0x8000:
        return out
    off = 12
    try:
        for _ in range(qd):
            _, off = read_name(pkt, off)
            off += 4
        for _ in range(an + ns + ar):
            name, off = read_name(pkt, off)
            if off + 10 > len(pkt):
                break
            rtype, _cls, _ttl, rdlen = struct.unpack_from(">HHIH", pkt, off)
            off += 10
            if rtype == 1 and rdlen == 4 and name.endswith(".local"):
                host = name[: -len(".local")]
                if host and "._" not in host:
                    out.append((socket.inet_ntoa(pkt[off:off + 4]), host[:63]))
            off += rdlen
    except DnsParseError:
        pass
    return out


# ---------------------------------------------------------------------------
# The forwarder
# ---------------------------------------------------------------------------
class _Pending:
    __slots__ = ("client", "orig_id", "query", "q_end", "qname", "qtype", "t0", "tries", "up_idx", "sent")

    def __init__(self, client, orig_id, query, q_end, qname, qtype, t0):
        self.client, self.orig_id, self.query, self.q_end = client, orig_id, query, q_end
        self.qname, self.qtype, self.t0 = qname, qtype, t0
        self.tries, self.up_idx, self.sent = 0, 0, t0


class NetObserve:
    """The DNS forwarder + discovery. Callbacks run on the forwarder threads."""

    def __init__(self, *, upstreams: list[str] | None = None, port: int = 53, bind: str = "",
                 on_flag: Callable[[dict], None] | None = None,
                 tcp: bool = True, discovery: bool = True):
        self.upstreams = [u for u in (upstreams or list(DEFAULT_UPSTREAMS)) if u][: P.LIMITS["dnsUpstreams"]]
        for u in self.upstreams:
            socket.inet_aton(u)
        self.port = port
        self.bind = bind
        self.on_flag = on_flag
        self.want_tcp = tcp
        self.want_discovery = discovery
        self.watchlist = Watchlist()
        self.clients = ClientTable()
        self.log: deque[dict] = deque(maxlen=LOG_MAX)
        self._batch: list[dict] = []
        self._batch_lock = threading.Lock()
        self._last_flag: dict[tuple[str, str], float] = {}
        self.cache: OrderedDict[tuple[str, int, int], tuple[bytes, float]] = OrderedDict()
        self.cache_lock = threading.Lock()
        self.pending: dict[int, _Pending] = {}
        self.stats = {"queries": 0, "forwarded": 0, "cacheHits": 0, "upstreamFail": 0,
                      "dropped": 0, "flagged": 0, "batchDropped": 0}
        self.up_stats = {u: [0, 0, 0.0] for u in self.upstreams}       # ok, fail, mean ms
        self.caps = 0
        self.running = False
        self.bind_error = ""
        self.last_client_query = 0.0
        self._own_ips: set[str] = set()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sel: selectors.BaseSelector | None = None
        self._listen: socket.socket | None = None
        self._up: socket.socket | None = None
        self._tcp: socketserver.ThreadingTCPServer | None = None
        self._scan_until = 0.0

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> bool:
        self._own_ips = _local_ipv4s()
        try:
            self._listen = self._bind_udp(self.port)
        except OSError as e:
            self.bind_error = f"UDP {self.bind or '0.0.0.0'}:{self.port}: {e}"
            log.error("DNS forwarder could not bind: %s", self.bind_error)
            return False
        self.port = self._listen.getsockname()[1]
        self._up = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._up.bind(("0.0.0.0", 0))
        self._up.setblocking(False)
        self._sel = selectors.DefaultSelector()
        self._sel.register(self._listen, selectors.EVENT_READ, "client")
        self._sel.register(self._up, selectors.EVENT_READ, "upstream")
        self.caps = P.NETCAP_DNS_FORWARD | P.NETCAP_DNS_CACHE | P.NETCAP_ARP_SCAN
        self._spawn(self._udp_loop, "dns-udp")
        if self.want_tcp:
            try:
                self._start_tcp()
                self.caps |= P.NETCAP_DNS_TCP
            except OSError as e:
                log.warning("DNS over TCP not available on :%s (%s)", self.port, e)
        if self.want_discovery:
            if self._start_listener(5353, self._mdns_loop, mcast="224.0.0.251"):
                self.caps |= P.NETCAP_MDNS_LISTEN
            if self._start_listener(67, self._dhcp_loop):
                self.caps |= P.NETCAP_DHCP_SNIFF
        self._spawn(self._arp_loop, "dns-arp")
        self.running = True
        log.info("DNS forwarder on %s:%s -> %s (caps 0x%02x)", self.bind or "0.0.0.0", self.port,
                 ",".join(self.upstreams), self.caps)
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._tcp:
            self._tcp.shutdown()
            self._tcp.server_close()
        for t in self._threads:
            t.join(timeout=2)
        for s in (self._listen, self._up):
            if s:
                s.close()
        self.running = False

    def _spawn(self, fn, name: str) -> None:
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def _bind_udp(self, port: int) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((self.bind or "0.0.0.0", port))
        except OSError:
            if self.bind:
                s.close()
                raise
            # 0.0.0.0:53 taken (e.g. a stub resolver on 127.0.0.53): bind the LAN address.
            s.close()
            lan = _primary_ipv4()
            if not lan:
                raise
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind((lan, port))
            self.bind = lan
        s.setblocking(False)
        return s

    # -- policy --------------------------------------------------------------
    def client_allowed(self, ip: str) -> bool:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return a.is_private or a.is_loopback or a.is_link_local

    # -- UDP path ------------------------------------------------------------
    def _udp_loop(self) -> None:
        assert self._sel
        while not self._stop.is_set():
            try:
                events = self._sel.select(timeout=0.25)
            except OSError:
                break
            for key, _ in events:
                sock = key.fileobj
                while True:
                    try:
                        data, addr = sock.recvfrom(4096)              # type: ignore[union-attr]
                    except (BlockingIOError, InterruptedError):
                        break
                    except OSError:
                        break
                    try:
                        if key.data == "client":
                            self._on_client(data, addr)
                        else:
                            self._on_upstream(data, addr)
                    except Exception:                                 # noqa: BLE001 - one bad packet never stops DNS
                        log.debug("dns packet handling failed", exc_info=True)
            self._expire()

    def _on_client(self, data: bytes, addr) -> None:
        ip = addr[0]
        if not self.client_allowed(ip):
            self.stats["dropped"] += 1
            return
        try:
            qname, qtype, qclass, q_end = parse_query(data)
        except DnsParseError:
            self.stats["dropped"] += 1
            return
        self.stats["queries"] += 1
        now = time.monotonic()
        if ip not in self._own_ips:
            self.last_client_query = now
        hit = self._cache_get(qname, qtype, qclass)
        if hit is not None:
            resp = data[:2] + hit[2:12] + data[12:q_end] + hit[q_end:] if len(hit) >= q_end else None
            if resp is not None:
                # Book-keeping BEFORE the reply: a client that asks again, or reads the
                # counters / log right after the answer, must already see this query.
                self.stats["cacheHits"] += 1
                self._record(ip, qname, qtype, 0, 0.0, P.DNSQ_CACHED)
                self._send_client(resp, addr)
                return
        if len(self.pending) >= PENDING_MAX:
            self.stats["dropped"] += 1
            self._send_client(servfail_for(data, q_end), addr)
            return
        new_id = self._new_id()
        p = _Pending(addr, data[:2], data, q_end, qname, qtype, now)
        self.pending[new_id] = p
        self._send_upstream(new_id, p)

    def _new_id(self) -> int:
        while True:
            i = random.getrandbits(16)
            if i not in self.pending:
                return i

    def _send_upstream(self, new_id: int, p: _Pending) -> None:
        up = self.upstreams[p.up_idx % len(self.upstreams)]
        p.tries += 1
        p.sent = time.monotonic()
        try:
            self._up.sendto(struct.pack(">H", new_id) + p.query[2:], (up, 53))   # type: ignore[union-attr]
        except OSError:
            self.up_stats[up][1] += 1

    def _on_upstream(self, data: bytes, addr) -> None:
        if len(data) < 12 or addr[0] not in self.up_stats:
            return
        new_id = struct.unpack_from(">H", data)[0]
        p = self.pending.get(new_id)
        if p is None or not data[2] & 0x80:
            return
        # The answer must be for the question we asked (defence against spoofed ids).
        try:
            qname, qtype, _qc, _q_end = _question_of(data)
        except DnsParseError:
            return
        if qname != p.qname or qtype != p.qtype:
            return
        del self.pending[new_id]
        latency = (time.monotonic() - p.t0) * 1000.0
        st = self.up_stats[addr[0]]
        st[0] += 1
        st[2] += (latency - st[2]) / min(st[0], 64)
        resp = p.orig_id + data[2:]
        self.stats["forwarded"] += 1
        try:
            rcode, an, tc, ttl = response_info(data)
        except DnsParseError:
            rcode, an, tc, ttl = 2, 0, False, -1
        # Cache + log BEFORE the reply: the client's next question (often immediate) must hit
        # the cache, and the query must already be in the log when the answer arrives.
        if rcode == 0 and an > 0 and not tc and ttl >= 0:
            self._cache_put(p.qname, p.qtype, 1, data, ttl)
        self._record(p.client[0], p.qname, p.qtype, rcode, latency, P.DNSQ_TRUNCATED if tc else 0)
        self._send_client(resp, p.client)

    def _expire(self) -> None:
        now = time.monotonic()
        for new_id, p in list(self.pending.items()):
            age = now - p.t0
            if age >= GIVE_UP_AFTER_S:
                del self.pending[new_id]
                self.stats["upstreamFail"] += 1
                up = self.upstreams[p.up_idx % len(self.upstreams)]
                self.up_stats[up][1] += 1
                self._record(p.client[0], p.qname, p.qtype, 2, age * 1000.0, P.DNSQ_UPSTREAM_FAILED)
                self._send_client(servfail_for(p.query, p.q_end), p.client)
            elif p.tries == 1 and now - p.sent >= RETRY_AFTER_S:
                self.up_stats[self.upstreams[p.up_idx % len(self.upstreams)]][1] += 1
                p.up_idx += 1                      # second upstream (or the same one again)
                self._send_upstream(new_id, p)

    def _send_client(self, resp: bytes, addr) -> None:
        try:
            self._listen.sendto(resp, addr)                               # type: ignore[union-attr]
        except OSError:
            pass

    # -- cache -----------------------------------------------------------------
    def _cache_get(self, qname: str, qtype: int, qclass: int) -> bytes | None:
        key = (qname, qtype, qclass)
        with self.cache_lock:
            hit = self.cache.get(key)
            if hit is None:
                return None
            if hit[1] <= time.monotonic():
                del self.cache[key]
                return None
            self.cache.move_to_end(key)
            return hit[0]

    def _cache_put(self, qname: str, qtype: int, qclass: int, resp: bytes, ttl: int) -> None:
        with self.cache_lock:
            self.cache[(qname, qtype, qclass)] = (resp, time.monotonic() + max(TTL_MIN, min(ttl, TTL_MAX)))
            while len(self.cache) > CACHE_MAX:
                self.cache.popitem(last=False)

    def flush_cache(self) -> int:
        with self.cache_lock:
            n = len(self.cache)
            self.cache.clear()
        return n

    # -- TCP path ----------------------------------------------------------------
    def _start_tcp(self) -> None:
        nob = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                self.request.settimeout(5)
                ip = self.client_address[0]
                if not nob.client_allowed(ip):
                    return
                while True:
                    hdr = _recv_exact(self.request, 2)
                    if not hdr:
                        return
                    q = _recv_exact(self.request, struct.unpack(">H", hdr)[0])
                    if not q:
                        return
                    nob._tcp_query(q, self.request, ip)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._tcp = Server((self.bind or "0.0.0.0", self.port), Handler)
        self._spawn(self._tcp.serve_forever, "dns-tcp")

    def _tcp_query(self, q: bytes, conn: socket.socket, ip: str) -> None:
        try:
            qname, qtype, _qc, q_end = parse_query(q)
        except DnsParseError:
            self.stats["dropped"] += 1
            return
        self.stats["queries"] += 1
        t0 = time.monotonic()
        for up in self.upstreams:
            try:
                with socket.create_connection((up, 53), timeout=GIVE_UP_AFTER_S) as s:
                    s.sendall(struct.pack(">H", len(q)) + q)
                    hdr = _recv_exact(s, 2)
                    resp = _recv_exact(s, struct.unpack(">H", hdr)[0]) if hdr else b""
                if len(resp) < 12:
                    raise OSError("short TCP answer")
                conn.sendall(struct.pack(">H", len(resp)) + resp)
                latency = (time.monotonic() - t0) * 1000.0
                self.stats["forwarded"] += 1
                self.up_stats[up][0] += 1
                rcode = resp[3] & 0xF
                self._record(ip, qname, qtype, rcode, latency, 0)
                return
            except OSError:
                self.up_stats[up][1] += 1
        self.stats["upstreamFail"] += 1
        fail = servfail_for(q, q_end)
        try:
            conn.sendall(struct.pack(">H", len(fail)) + fail)
        except OSError:
            pass
        self._record(ip, qname, qtype, 2, (time.monotonic() - t0) * 1000.0, P.DNSQ_UPSTREAM_FAILED)

    # -- recording ----------------------------------------------------------------
    def _record(self, ip: str, qname: str, qtype: int, rcode: int, latency_ms: float, flags: int) -> None:
        e = self.clients.touch(ip, source=P.DISC_DNS, query=True)
        hit = self.watchlist.match(qname)
        cat = 0
        if hit:
            suffix, cat, rflags = hit
            flags |= P.DNSQ_FLAGGED
            self.stats["flagged"] += 1
            if rflags & P.RULE_ALERT:
                self._maybe_flag(ip, e, qname, qtype, suffix, cat)
        rec = {"t": time.monotonic(), "ip": ip, "qname": qname[:253], "qtype": qtype, "rcode": rcode,
               "latency": int(min(latency_ms, 65535)), "flags": flags, "category": cat}
        self.log.append(rec)
        with self._batch_lock:
            if len(self._batch) >= LOG_MAX:
                self.stats["batchDropped"] += 1
            else:
                self._batch.append(rec)

    def _maybe_flag(self, ip: str, e: dict, qname: str, qtype: int, suffix: str, cat: int) -> None:
        key = (ip, suffix)
        now = time.monotonic()
        if now - self._last_flag.get(key, -1e9) < FLAG_REPEAT_S:
            return
        self._last_flag[key] = now
        if len(self._last_flag) > 4096:
            self._last_flag = {k: v for k, v in self._last_flag.items() if now - v < FLAG_REPEAT_S}
        if self.on_flag:
            self.on_flag({"ip": ip, "mac": e.get("mac") or b"", "hostname": e.get("hostname") or "",
                          "qname": qname[:253], "qtype": qtype, "category": cat, "rule": suffix,
                          "t": now, "watchVer": self.watchlist.version})

    # -- batches -----------------------------------------------------------------
    def take_batch(self) -> tuple[list[dict], int]:
        with self._batch_lock:
            batch, self._batch = self._batch, []
            dropped, self.stats["batchDropped"] = self.stats["batchDropped"], 0
        return batch, dropped

    def batch_frames(self, batch: list[dict], *, now: float, max_body: int) -> list[tuple[list, list]]:
        """Fold identical (client, qname, qtype) and split into (clients, queries) chunks
        whose CBOR fits max_body bytes. Newest occurrence wins ageMs."""
        import ewp_cbor
        folded: OrderedDict[tuple[str, str, int], dict] = OrderedDict()
        for r in batch:
            k = (r["ip"], r["qname"], r["qtype"])
            f = folded.get(k)
            if f is None:
                folded[k] = dict(r, count=1)
            else:
                f["count"] = min(f["count"] + 1, 0xFFFF)
                f["flags"] |= r["flags"]
                if r["t"] >= f["t"]:
                    f.update(t=r["t"], rcode=r["rcode"], latency=r["latency"])
                f["category"] = f["category"] or r["category"]
        chunks: list[tuple[list, list]] = []
        clients: list = []
        index: dict[str, int] = {}
        queries: list = []
        size = 32
        for r in folded.values():
            ip = r["ip"]
            new_client = ip not in index
            ce = None
            if new_client:
                e = self.clients.get(ip) or {}
                ce = [socket.inet_aton(ip), e.get("mac") or b"", e.get("hostname") or ""]
            q_idx = index.get(ip, len(clients))
            q = [max(0, int((now - r["t"]) * 1000)), q_idx, r["qname"], r["qtype"], r["rcode"],
                 r["latency"], r["count"], r["flags"], r["category"]]
            add = len(ewp_cbor.dumps(q)) + (len(ewp_cbor.dumps(ce)) if ce else 0)
            if queries and (size + add > max_body or (new_client and len(clients) >= 255)):
                chunks.append((clients, queries))
                clients, index, queries, size = [], {}, [], 32
                e = self.clients.get(ip) or {}
                ce = [socket.inet_aton(ip), e.get("mac") or b"", e.get("hostname") or ""]
                new_client = True
                q[1] = 0
                add = len(ewp_cbor.dumps(q)) + len(ewp_cbor.dumps(ce))
            if new_client:
                index[ip] = len(clients)
                clients.append(ce)
            queries.append(q)
            size += add
        if queries:
            chunks.append((clients, queries))
        return chunks

    def query_log(self, limit: int, client: str | None, flagged_only: bool) -> tuple[list, list]:
        now = time.monotonic()
        rows = []
        for r in reversed(self.log):
            if client and r["ip"] != client:
                continue
            if flagged_only and not r["flags"] & P.DNSQ_FLAGGED:
                continue
            rows.append(r)
            if len(rows) >= limit:
                break
        clients: list = []
        index: dict[str, int] = {}
        queries = []
        for r in rows:
            if r["ip"] not in index:
                e = self.clients.get(r["ip"]) or {}
                index[r["ip"]] = len(clients)
                clients.append([socket.inet_aton(r["ip"]), e.get("mac") or b"", e.get("hostname") or ""])
            queries.append([int((now - r["t"]) * 1000), index[r["ip"]], r["qname"], r["qtype"], r["rcode"],
                            r["latency"], 1, r["flags"], r["category"]])
        return clients, queries

    def counters(self) -> dict:
        return {"mode": P.DNS_MODE_FORWARD if self.running else P.DNS_MODE_OFF,
                "queries": self.stats["queries"] & 0xFFFFFFFF,
                "forwarded": self.stats["forwarded"] & 0xFFFFFFFF,
                "cacheHits": self.stats["cacheHits"] & 0xFFFFFFFF,
                "upstreamFail": self.stats["upstreamFail"] & 0xFFFFFFFF,
                "dropped": self.stats["dropped"] & 0xFFFFFFFF,
                "flagged": self.stats["flagged"] & 0xFFFFFFFF,
                "clients": min(self.clients.recent_clients(), 0xFFFF),
                "lanDevices": min(len(self.clients.t), 0xFFFF)}

    def upstream_rows(self) -> list:
        return [[socket.inet_aton(u), s[0] & 0xFFFFFFFF, s[1] & 0xFFFFFFFF, int(min(s[2], 65535))]
                for u, s in self.up_stats.items()]

    # -- discovery ------------------------------------------------------------------
    def _start_listener(self, port: int, loop, mcast: str | None = None) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            s.bind(("0.0.0.0", port))
            if mcast:
                mreq = socket.inet_aton(mcast) + socket.inet_aton("0.0.0.0")
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            s.settimeout(0.5)
        except OSError as e:
            log.info("passive listener on UDP %s not available (%s)", port, e)
            return False
        self._spawn(lambda: loop(s), f"disc-{port}")
        return True

    def _mdns_loop(self, s: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data, _ = s.recvfrom(9000)
            except (socket.timeout, OSError):
                continue
            for ip, host in parse_mdns(data):
                if self.client_allowed(ip):
                    self.clients.touch(ip, hostname=host, source=P.DISC_MDNS)
        s.close()

    def _dhcp_loop(self, s: socket.socket) -> None:
        while not self._stop.is_set():
            try:
                data, _ = s.recvfrom(1500)
            except (socket.timeout, OSError):
                continue
            r = parse_dhcp(data)
            if not r:
                continue
            mac, host, ip = r
            if ip and self.client_allowed(ip):
                self.clients.touch(ip, mac=mac, hostname=host, source=P.DISC_DHCP)
            else:
                self.clients.by_mac(mac, host)
        s.close()

    def _arp_loop(self) -> None:
        while not self._stop.is_set():
            self.clients.refresh_arp()
            self._stop.wait(1.0 if time.monotonic() < self._scan_until else 15.0)

    def scan_lan(self) -> tuple[int, int]:
        """One empty UDP datagram to every host of each private /24 we sit on: the
        kernel must ARP each address first, so every live device lands in
        /proc/net/arp. No root, no extra tools."""
        targets = []
        for ip in self._own_ips or _local_ipv4s():
            a = ipaddress.ip_address(ip)
            if a.is_private and not a.is_loopback and not a.is_link_local:
                targets += [str(h) for h in ipaddress.ip_network(f"{ip}/24", strict=False).hosts() if str(h) != ip]
        eta_ms = 2500 + len(targets) * 5
        self._scan_until = time.monotonic() + eta_ms / 1000.0 + 5

        def run():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setblocking(False)
                for i, t in enumerate(targets):
                    try:
                        s.sendto(b"", (t, 9))
                    except OSError:
                        pass
                    if i % 32 == 31:
                        time.sleep(0.1)
        threading.Thread(target=run, name="arp-scan", daemon=True).start()
        return len(targets), eta_ms


def _question_of(resp: bytes) -> tuple[str, int, int, int]:
    if len(resp) < 12:
        raise DnsParseError("short")
    qd = struct.unpack_from(">H", resp, 4)[0]
    if qd != 1:
        raise DnsParseError("one question expected")
    name, off = read_name(resp, 12)
    if off + 4 > len(resp):
        raise DnsParseError("truncated question")
    qtype, qclass = struct.unpack_from(">HH", resp, off)
    return name, qtype, qclass, off + 4


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        try:
            chunk = s.recv(n - len(buf))
        except OSError:
            return b""
        if not chunk:
            return b""
        buf += chunk
    return buf


def _local_ipv4s() -> set[str]:
    out = {"127.0.0.1"}
    p = _primary_ipv4()
    if p:
        out.add(p)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.add(info[4][0])
    except OSError:
        pass
    return out


def _primary_ipv4() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))                 # TEST-NET: no packet leaves for UDP connect
            return s.getsockname()[0]
    except OSError:
        return None
