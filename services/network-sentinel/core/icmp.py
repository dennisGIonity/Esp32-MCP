"""
AEDI - IONITY GLOBAL | Network sentinel | core/icmp.py   Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

One ICMP echo, standard library only (same approach as the Pi agent's pinger):
unprivileged SOCK_DGRAM ICMP first (Linux ping_group_range), raw socket with
CAP_NET_RAW second, IcmpNotPermitted otherwise - never a made-up number.
"""
from __future__ import annotations

import os
import socket
import struct
import time


class IcmpNotPermitted(OSError):
    """Neither an unprivileged nor a raw ICMP socket may be opened here."""


def _checksum(b: bytes) -> int:
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def _open() -> tuple[socket.socket, bool]:
    for kind, raw in ((socket.SOCK_DGRAM, False), (socket.SOCK_RAW, True)):
        try:
            return socket.socket(socket.AF_INET, kind, socket.IPPROTO_ICMP), raw
        except OSError as e:
            if e.errno in (1, 13, 93, 94):      # EPERM, EACCES, EPROTONOSUPPORT, ESOCKTNOSUPPORT
                continue
            raise
    raise IcmpNotPermitted("no ICMP socket: run with CAP_NET_RAW or widen net.ipv4.ping_group_range")


_seq = 0


def echo(ip: str, timeout_s: float = 1.0) -> float | None:
    """RTT in ms of one echo, or None when no reply arrived in time. Blocking."""
    global _seq
    sock, raw = _open()
    try:
        _seq = (_seq + 1) & 0xFFFF
        ident = os.getpid() & 0xFFFF
        payload = struct.pack("!d", time.monotonic())
        hdr = struct.pack("!BBHHH", 8, 0, 0, ident, _seq)
        sock.sendto(struct.pack("!BBHHH", 8, 0, _checksum(hdr + payload), ident, _seq) + payload, (ip, 0))
        t0 = time.monotonic()
        until = t0 + timeout_s
        while (left := until - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                pkt, frm = sock.recvfrom(1024)
            except (socket.timeout, TimeoutError):
                return None
            if frm[0] != ip:
                continue
            if raw:
                pkt = pkt[(pkt[0] & 0x0F) * 4:]
            if len(pkt) >= 8:
                t, _c, _k, rid, seq = struct.unpack("!BBHHH", pkt[:8])
                if t == 0 and seq == _seq and (not raw or rid == ident):
                    return (time.monotonic() - t0) * 1000.0
        return None
    finally:
        sock.close()
