"""
AEDI - IONITY GLOBAL | Ionity fleet agent 2 | pinger.py - ping_host (EWP tool 0x47)
Doc ID: DOC-2026-10-ESP32MCP-PIAGENT2 | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

ICMP echo from the Pi itself, standard library only, never `subprocess ping`:

  1. unprivileged ICMP: socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP) - works when the
     agent's group is inside net.ipv4.ping_group_range (Raspberry Pi OS default:
     "0 2147483647"); the kernel sets the echo id and strips the IP header.
  2. raw socket (root / CAP_NET_RAW) when (1) is refused.
  3. neither -> error NOT_PERMITTED, nothing invented.

One session at a time per agent (BUSY otherwise). Bounds = EWP_LIMITS.ping*.
"""
from __future__ import annotations

import os
import re
import socket
import struct
import threading
import time
from typing import Callable

import ewp_protocol as P

_TARGET_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$")

ICMP_ECHO_REQUEST = 8
ICMP_ECHO_REPLY = 0


def valid_target(t: str) -> bool:
    return bool(_TARGET_RE.match(t))


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def echo_request(ident: int, seq: int, payload: bytes) -> bytes:
    hdr = struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, 0, ident, seq)
    return struct.pack("!BBHHH", ICMP_ECHO_REQUEST, 0, checksum(hdr + payload), ident, seq) + payload


def parse_reply(pkt: bytes, raw: bool) -> tuple[int, int, int] | None:
    """-> (type, id, seq) of an ICMP message, IP header skipped for raw sockets."""
    if raw:
        if len(pkt) < 20:
            return None
        pkt = pkt[(pkt[0] & 0x0F) * 4:]
    if len(pkt) < 8:
        return None
    t, _code, _ck, ident, seq = struct.unpack("!BBHHH", pkt[:8])
    return t, ident, seq


class Pinger:
    """ping(target, count, timeout_ms, interval_ms) -> PingHostResult fields (named)."""

    def __init__(self, sock_factory: Callable[..., socket.socket] = socket.socket,
                 resolve: Callable[[str], str] = socket.gethostbyname,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._sock = sock_factory
        self._resolve = resolve
        self._clock = clock
        self._sleep = sleep
        self._busy = threading.Lock()
        self._seq = 0

    def _open(self) -> tuple[socket.socket, bool] | None:
        for kind, raw in ((socket.SOCK_DGRAM, False), (socket.SOCK_RAW, True)):
            try:
                return self._sock(socket.AF_INET, kind, socket.IPPROTO_ICMP), raw
            except PermissionError:
                continue
            except OSError as e:          # EPERM / EACCES / EPROTONOSUPPORT
                if e.errno in (1, 13, 93, 94):
                    continue
                raise
        return None

    def ping(self, target: str, count: int = 4, timeout_ms: int = 1000, interval_ms: int = 1000) -> dict:
        if not self._busy.acquire(blocking=False):
            return {"error": P.PING_BUSY, "sent": 0, "received": 0}
        try:
            return self._ping(target, count, timeout_ms, interval_ms)
        finally:
            self._busy.release()

    def _ping(self, target: str, count: int, timeout_ms: int, interval_ms: int) -> dict:
        try:
            ip = self._resolve(target)
            ip_bytes = socket.inet_aton(ip)
        except (OSError, UnicodeError):
            return {"error": P.PING_RESOLVE_FAILED, "sent": 0, "received": 0}
        opened = self._open()
        if opened is None:
            return {"error": P.PING_NOT_PERMITTED, "resolvedIp": ip_bytes, "sent": 0, "received": 0}
        sock, raw = opened
        ident = os.getpid() & 0xFFFF
        rtts: list[float] = []
        sent = 0
        try:
            for i in range(count):
                if i:
                    self._sleep(interval_ms / 1000.0)
                self._seq = (self._seq + 1) & 0xFFFF
                seq = self._seq
                t0 = self._clock()
                sock.sendto(echo_request(ident, seq, struct.pack("!d", t0)), (ip, 0))
                sent += 1
                until = t0 + timeout_ms / 1000.0
                while True:
                    left = until - self._clock()
                    if left <= 0:
                        break
                    sock.settimeout(left)
                    try:
                        pkt, frm = sock.recvfrom(1024)
                    except (socket.timeout, TimeoutError):
                        break
                    if frm[0] != ip:
                        continue
                    r = parse_reply(pkt, raw)
                    # DGRAM sockets: the kernel owns the id, so match on seq alone
                    if r and r[0] == ICMP_ECHO_REPLY and r[2] == seq and (not raw or r[1] == ident):
                        rtts.append((self._clock() - t0) * 1000.0)
                        break
        finally:
            sock.close()
        out = {"error": P.PING_NONE if rtts else P.PING_TIMEOUT, "resolvedIp": ip_bytes,
               "sent": sent, "received": len(rtts)}
        if rtts:
            clamp = lambda v: min(int(round(v)), 0xFFFF)  # noqa: E731
            out.update(rttMinMs=clamp(min(rtts)), rttAvgMs=clamp(sum(rtts) / len(rtts)), rttMaxMs=clamp(max(rtts)))
        return out
