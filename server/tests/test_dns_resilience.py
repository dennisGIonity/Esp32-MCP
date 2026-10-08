"""
AEDI - IONITY GLOBAL | LAN DNS resolver: listener must survive clients that hang up
Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd | AEDI
Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
Owner: github.com/Ionity-Global | www.ionity.today | ai@ionity.today

Regression for 2026-10-07: on a multi-homed Windows host the DNS client asks
every adapter and closes its socket as soon as one answers. Our late reply then
hit a closed port; Windows reported WSAECONNRESET on the listener, which died
silently while stats still said "running".
"""
import asyncio
import socket
import struct
from types import SimpleNamespace

from app.ingest.dns_resolver import DnsService, _ServerProtocol


def _settings(port: int) -> SimpleNamespace:
    # Upstream points at a closed local port: every forward fails fast, so the
    # resolver answers SERVFAIL -- a reply we can send to clients that left.
    return SimpleNamespace(dns_upstreams="127.0.0.1", dns_bind="127.0.0.1", dns_port=port,
                           dns_allow_from="127.0.0.0/8", dns_timeout_s=0.2)


def _query(name: str, txid: int) -> bytes:
    q = struct.pack(">HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    return q + struct.pack(">HH", 1, 1)


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_error_received_is_counted_not_fatal():
    svc = DnsService(store=None, settings=_settings(0))
    _ServerProtocol(svc).error_received(ConnectionResetError(10054, "reset by peer"))
    assert svc.recv_errors == 1


def test_listener_survives_clients_that_hang_up():
    async def run():
        port = _free_udp_port()
        svc = DnsService(store=None, settings=_settings(port))
        svc.upstreams = [("127.0.0.1", _free_udp_port())]
        await svc._bind()
        svc.running = True
        try:
            for i in range(25):                       # ask, then hang up at once
                c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                c.sendto(_query(f"gone{i}.example", 0x1000 + i), ("127.0.0.1", port))
                c.close()
            await asyncio.sleep(1.5)                  # late replies hit closed ports
            loop = asyncio.get_running_loop()
            c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            c.setblocking(False)
            c.sendto(_query("still.alive.example", 0x4242), ("127.0.0.1", port))
            data = await asyncio.wait_for(loop.sock_recv(c, 512), timeout=3)
            c.close()
            assert data[:2] == b"\x42\x42"            # the listener still answers
            assert svc.rebinds == 0 or svc.running
        finally:
            await svc.stop()
    asyncio.run(run())
