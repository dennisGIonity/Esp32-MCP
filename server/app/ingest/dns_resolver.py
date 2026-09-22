"""
AEDI - IONITY GLOBAL | LAN DNS logging resolver
Doc ID: DOC-2026-09-ESP32MCP-DNS | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

Why this lives on the server and not on an ESP32
------------------------------------------------
A WiFi station cannot see another station's traffic: WPA2/WPA3 gives each
client its own pairwise key, and the AP switches unicast frames rather than
flooding them. So sniffing other devices' DNS from an ESP32 is impossible.

Instead we make the traffic come to us. Point the router's DHCP at this host
as the LAN DNS server and every device's queries arrive here by design. We
log who asked for what, then forward upstream and return the real answer.

Pure asyncio + stdlib wire-format parsing -- no extra dependency.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import time
from typing import Any

log = logging.getLogger("ionity.dns")

QTYPES = {1: "A", 2: "NS", 5: "CNAME", 6: "SOA", 12: "PTR", 15: "MX",
          16: "TXT", 28: "AAAA", 33: "SRV", 65: "HTTPS", 64: "SVCB"}


# ---------------------------------------------------------------------------
# Minimal DNS wire-format helpers
# ---------------------------------------------------------------------------
def _read_name(buf: bytes, off: int) -> tuple[str, int]:
    """Read a (possibly compressed) DNS name. Returns (name, next_offset)."""
    labels: list[str] = []
    jumped = False
    next_off = off
    hops = 0
    while True:
        if off >= len(buf) or hops > 32:
            break
        ln = buf[off]
        if ln == 0:
            off += 1
            if not jumped:
                next_off = off
            break
        if ln & 0xC0 == 0xC0:                      # compression pointer
            if off + 1 >= len(buf):
                break
            ptr = struct.unpack_from("!H", buf, off)[0] & 0x3FFF
            if not jumped:
                next_off = off + 2
            off = ptr
            jumped = True
            hops += 1
            continue
        off += 1
        labels.append(buf[off:off + ln].decode("utf-8", "replace"))
        off += ln
        if not jumped:
            next_off = off
    return ".".join(labels), next_off


def parse_question(pkt: bytes) -> tuple[str, str] | None:
    """Return (qname, qtype_label) from a DNS query packet."""
    if len(pkt) < 12:
        return None
    qdcount = struct.unpack_from("!H", pkt, 4)[0]
    if qdcount < 1:
        return None
    name, off = _read_name(pkt, 12)
    if off + 4 > len(pkt):
        return None
    qtype = struct.unpack_from("!H", pkt, off)[0]
    return name.lower(), QTYPES.get(qtype, str(qtype))


def parse_answers(pkt: bytes) -> tuple[list[str], int]:
    """Extract A/AAAA/CNAME answers and the response code."""
    out: list[str] = []
    if len(pkt) < 12:
        return out, -1
    flags = struct.unpack_from("!H", pkt, 2)[0]
    rcode = flags & 0x000F
    qd, an = struct.unpack_from("!HH", pkt, 4)
    off = 12
    for _ in range(qd):
        _, off = _read_name(pkt, off)
        off += 4
    for _ in range(an):
        if off >= len(pkt):
            break
        _, off = _read_name(pkt, off)
        if off + 10 > len(pkt):
            break
        rtype, _rcls, _ttl, rdlen = struct.unpack_from("!HHIH", pkt, off)
        off += 10
        rdata = pkt[off:off + rdlen]
        off += rdlen
        try:
            if rtype == 1 and rdlen == 4:
                out.append(".".join(str(b) for b in rdata))
            elif rtype == 28 and rdlen == 16:
                out.append(":".join(f"{rdata[i]:02x}{rdata[i+1]:02x}"
                                    for i in range(0, 16, 2)))
            elif rtype == 5:
                cname, _ = _read_name(pkt, off - rdlen)
                out.append("CNAME " + cname)
        except Exception:
            pass
    return out, rcode


def min_ttl(pkt: bytes, default: int = 60) -> int:
    """Smallest TTL in the answer section, clamped to something sane."""
    try:
        qd, an = struct.unpack_from("!HH", pkt, 4)
        if an == 0:
            return default
        off = 12
        for _ in range(qd):
            _, off = _read_name(pkt, off)
            off += 4
        ttls = []
        for _ in range(an):
            _, off = _read_name(pkt, off)
            if off + 10 > len(pkt):
                break
            _rt, _rc, ttl, rdlen = struct.unpack_from("!HHIH", pkt, off)
            ttls.append(ttl)
            off += 10 + rdlen
        if not ttls:
            return default
        return max(15, min(min(ttls), 3600))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------
class _ServerProtocol(asyncio.DatagramProtocol):
    def __init__(self, svc: "DnsService"):
        self.svc = svc

    def connection_made(self, transport):
        self.svc.transport = transport

    def datagram_received(self, data: bytes, addr):
        asyncio.create_task(self.svc.handle(data, addr))


class DnsService:
    """Logging DNS forwarder. Answers from cache when it can, otherwise
    relays to the upstream resolvers and caches the reply for its TTL."""

    def __init__(self, store, settings):
        self.store = store
        self.s = settings
        self.transport: asyncio.DatagramTransport | None = None
        self.upstreams: list[tuple[str, int]] = [
            (h.strip(), 53) for h in settings.dns_upstreams.split(",") if h.strip()
        ]
        self._cache: dict[tuple[str, str], tuple[bytes, float]] = {}
        self._pending: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._writer: asyncio.Task | None = None
        self._arp: asyncio.Task | None = None
        self._pruner: asyncio.Task | None = None
        self.recent: list[dict[str, Any]] = []      # small in-memory live feed

        self.running = False
        self.bind_error: str | None = None
        self.queries = 0
        self.cache_hits = 0
        self.upstream_fails = 0

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.create_datagram_endpoint(
                lambda: _ServerProtocol(self),
                local_addr=(self.s.dns_bind, self.s.dns_port),
            )
            self.running = True
            log.info("DNS resolver listening on %s:%s -> upstreams %s",
                     self.s.dns_bind, self.s.dns_port, self.upstreams)
        except Exception as e:
            self.bind_error = str(e)
            log.error("DNS resolver could not bind %s:%s -- %s",
                      self.s.dns_bind, self.s.dns_port, e)
            return

        self._writer = asyncio.create_task(self._writer_loop())
        self._arp = asyncio.create_task(self._arp_loop())
        self._pruner = asyncio.create_task(self._pruner_loop())

    async def stop(self) -> None:
        for t in (self._writer, self._arp, self._pruner):
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        if self.transport:
            self.transport.close()
        self.running = False

    # -- request path ------------------------------------------------------
    async def handle(self, data: bytes, addr) -> None:
        client_ip = addr[0]
        t0 = time.perf_counter()
        q = parse_question(data)
        if not q:
            return
        qname, qtype = q
        self.queries += 1
        key = (qname, qtype)
        txid = data[:2]

        # cache
        hit = self._cache.get(key)
        if hit and hit[1] > time.time():
            resp = txid + hit[0][2:]
            self.transport.sendto(resp, addr)
            self.cache_hits += 1
            answers, rcode = parse_answers(resp)
            self._record(client_ip, qname, qtype, answers, rcode, True,
                         (time.perf_counter() - t0) * 1000)
            return

        # forward
        resp = await self._forward(data)
        if resp is None:
            self.upstream_fails += 1
            # SERVFAIL so the client fails fast instead of hanging
            head = bytearray(data[:12])
            head[2] = 0x81
            head[3] = 0x82
            self.transport.sendto(bytes(head) + data[12:], addr)
            self._record(client_ip, qname, qtype, [], 2, False,
                         (time.perf_counter() - t0) * 1000)
            return

        self.transport.sendto(txid + resp[2:], addr)
        answers, rcode = parse_answers(resp)
        if rcode == 0 and answers:
            self._cache[key] = (resp, time.time() + min_ttl(resp))
            if len(self._cache) > 5000:
                now = time.time()
                for k, v in list(self._cache.items()):
                    if v[1] <= now:
                        self._cache.pop(k, None)
        self._record(client_ip, qname, qtype, answers, rcode, False,
                     (time.perf_counter() - t0) * 1000)

    async def _forward(self, data: bytes) -> bytes | None:
        loop = asyncio.get_running_loop()
        for host, port in self.upstreams:
            fut: asyncio.Future = loop.create_future()

            class _Cli(asyncio.DatagramProtocol):
                def connection_made(self, tr):
                    tr.sendto(data)

                def datagram_received(self, d, _a):
                    if not fut.done():
                        fut.set_result(d)

                def error_received(self, exc):
                    if not fut.done():
                        fut.set_exception(exc)

            try:
                tr, _ = await loop.create_datagram_endpoint(_Cli, remote_addr=(host, port))
                try:
                    return await asyncio.wait_for(fut, timeout=self.s.dns_timeout_s)
                finally:
                    tr.close()
            except Exception:
                continue
        return None

    # -- logging -----------------------------------------------------------
    def _record(self, client_ip, qname, qtype, answers, rcode, cached, latency_ms):
        row = {
            "ts": time.time(), "client_ip": client_ip, "qname": qname,
            "qtype": qtype, "answers": ",".join(answers[:6]) or None,
            "rcode": rcode, "cached": cached, "blocked": 0,
            "latency_ms": round(latency_ms, 2),
        }
        self._pending.append(row)
        self.recent.append(row)
        if len(self.recent) > 300:
            del self.recent[: len(self.recent) - 300]

    async def _writer_loop(self) -> None:
        """Batch DNS rows to disk. A busy LAN can easily do hundreds of
        queries a second; one commit per query would be wasteful."""
        while True:
            try:
                await asyncio.sleep(2.0)
                if not self._pending:
                    continue
                async with self._lock:
                    batch, self._pending = self._pending, []
                await self.store.insert_dns_batch(batch)
            except asyncio.CancelledError:
                if self._pending:
                    try:
                        await self.store.insert_dns_batch(self._pending)
                    except Exception:
                        pass
                break
            except Exception:
                log.exception("dns writer error")

    async def _pruner_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(3600)
                cutoff = time.time() - self.s.dns_retention_days * 86400
                n = await self.store.prune_dns(cutoff)
                if n:
                    log.info("Pruned %d DNS rows older than %d days",
                             n, self.s.dns_retention_days)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("dns pruner error")

    # -- LAN device naming -------------------------------------------------
    async def _arp_loop(self) -> None:
        """Give the IPs names. ARP supplies the MAC (hence vendor), and a
        best-effort reverse lookup often supplies a hostname."""
        while True:
            try:
                await self._refresh_arp()
                await asyncio.sleep(120)
            except asyncio.CancelledError:
                break
            except Exception:
                log.debug("arp refresh failed", exc_info=True)
                await asyncio.sleep(120)

    async def _refresh_arp(self) -> None:
        import re
        import socket

        # Name the host we are running on, so it isn't "unidentified" in the list.
        try:
            me = socket.gethostbyname(socket.gethostname())
            await self.store.upsert_lan_device(
                ip=me, hostname=socket.gethostname(),
                vendor="this host", label="Ionity fleet server")
            await self.store.upsert_lan_device(
                ip="127.0.0.1", hostname="localhost",
                vendor="this host", label="fleet server (loopback)")
        except Exception:
            pass

        # Name LAN devices the fleet already knows. An ESP32's device_id IS its
        # MAC ("esp32-98a316e5d18c" <-> 98-a3-16-e5-d1-8c), so the network map
        # and the fleet registry can cross-reference without any config.
        fleet_names: dict[str, str] = {}
        try:
            for d in await self.store.list_devices():
                did = d["device_id"]
                if did.startswith("esp32-") and len(did) == 18:
                    h = did[6:]
                    mac_key = "-".join(h[i:i + 2] for i in range(0, 12, 2))
                    fleet_names[mac_key] = d.get("label") or did
        except Exception:
            pass
        # Anything else (the Pi, a printer) can be named by MAC in
        # config/device_labels.json: {"88-a2-9e-27-a1-8f": {"label": "..."}}
        manual: dict[str, str] = {}
        try:
            import json as _json
            from pathlib import Path as _Path
            cfg = _Path(__file__).resolve().parents[3] / "config" / "device_labels.json"
            for k, v in _json.loads(cfg.read_text(encoding="utf-8")).items():
                if re.fullmatch(r"[0-9a-fA-F]{2}([-:][0-9a-fA-F]{2}){5}", k) and v.get("label"):
                    manual[k.lower().replace(":", "-")] = v["label"]
        except Exception:
            pass

        proc = await asyncio.create_subprocess_exec(
            "arp", "-a",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        text = out.decode("utf-8", "replace")

        pat = re.compile(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}"
                         r"[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}"
                         r"[-:][0-9a-fA-F]{2})")
        for ip, mac in pat.findall(text):
            mac = mac.lower().replace(":", "-")
            # Skip broadcast and multicast pseudo-entries: they are not devices.
            # 224.0.0.0/4 is multicast; 01-00-5e is the IPv4-multicast MAC prefix.
            first_octet = int(ip.split(".")[0])
            if (ip.endswith(".255") or mac.startswith("ff-ff")
                    or mac.startswith("01-00-5e") or 224 <= first_octet <= 239):
                continue
            hostname = None
            try:
                loop = asyncio.get_running_loop()
                hostname = (await asyncio.wait_for(
                    loop.getnameinfo((ip, 0), 0), timeout=1.5))[0]
                if hostname == ip:
                    hostname = None
            except Exception:
                hostname = None
            await self.store.upsert_lan_device(
                ip=ip, mac=mac, hostname=hostname, vendor=oui_vendor(mac),
                label=fleet_names.get(mac) or manual.get(mac))

        n = await self.store.prune_lan(time.time() - 2 * 3600)
        if n:
            log.info("LAN map: dropped %d address(es) not seen for 2h", n)

    # -- stats -------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "bind": f"{self.s.dns_bind}:{self.s.dns_port}",
            "bind_error": self.bind_error,
            "upstreams": [f"{h}:{p}" for h, p in self.upstreams],
            "queries": self.queries,
            "cache_hits": self.cache_hits,
            "cache_entries": len(self._cache),
            "cache_hit_rate": round(self.cache_hits / self.queries, 3) if self.queries else 0.0,
            "upstream_failures": self.upstream_fails,
            "pending_writes": len(self._pending),
        }


# ---------------------------------------------------------------------------
# OUI -> vendor, for the common consumer kit on a home/office LAN.
# Deliberately small: enough to make the device list readable without
# shipping a 30 MB IEEE registry.
# ---------------------------------------------------------------------------
_OUI = {
    "78-20-51": "Askey / ISP router", "00-1a-11": "Google",
    "3c-5a-b4": "Google", "f4-f5-d8": "Google", "1c-53-f9": "Amazon",
    "44-65-0d": "Amazon", "fc-65-de": "Amazon", "ac-63-be": "Amazon",
    "b8-27-eb": "Raspberry Pi", "dc-a6-32": "Raspberry Pi",
    "e4-5f-01": "Raspberry Pi", "2c-cf-67": "Raspberry Pi",
    "98-a3-16": "Espressif", "fc-01-2c": "Espressif", "f0-f5-bd": "Espressif",
    "34-85-18": "Espressif", "dc-54-75": "Espressif", "e8-06-90": "Espressif",
    "88-a2-9e": "Raspberry Pi", "d8-3a-dd": "Raspberry Pi", "28-cd-c1": "Raspberry Pi",
    "24-0a-c4": "Espressif", "30-ae-a4": "Espressif", "7c-9e-bd": "Espressif",
    "84-cc-a8": "Espressif", "a0-20-a6": "Espressif", "c4-4f-33": "Espressif",
    "d8-a0-1d": "Espressif", "ec-fa-bc": "Espressif", "f4-cf-a2": "Espressif",
    "48-3f-da": "Espressif", "8c-aa-b5": "Espressif", "10-52-1c": "Espressif",
    "00-50-56": "VMware", "00-0c-29": "VMware", "00-15-5d": "Microsoft Hyper-V",
    "00-1c-42": "Parallels", "08-00-27": "VirtualBox",
    "3c-22-fb": "Apple", "a4-83-e7": "Apple", "f0-18-98": "Apple",
    "dc-2b-2a": "Apple", "90-9c-4a": "Apple", "bc-d0-74": "Apple",
    "00-16-3e": "Xen", "52-54-00": "QEMU/KVM",
    "d0-37-45": "TP-Link", "50-c7-bf": "TP-Link", "a4-2b-b0": "TP-Link",
    "e8-de-27": "TP-Link", "14-cc-20": "TP-Link",
    "00-1e-58": "D-Link", "34-08-04": "D-Link",
    "00-24-01": "D-Link", "c0-a0-bb": "D-Link",
    "18-d6-c7": "TP-Link", "0c-80-63": "TP-Link",
    "b0-be-76": "TP-Link", "60-32-b1": "TP-Link",
    "e4-8d-8c": "Routerboard/MikroTik", "2c-c8-1b": "Routerboard/MikroTik",
    "48-8f-5a": "Routerboard/MikroTik", "dc-2c-6e": "Routerboard/MikroTik",
    "64-d1-54": "Routerboard/MikroTik", "6c-3b-6b": "Routerboard/MikroTik",
    "00-0c-42": "Routerboard/MikroTik",
    "ac-de-48": "private/randomised", "02-00-00": "private/randomised",
}


def oui_vendor(mac: str | None) -> str | None:
    if not mac or len(mac) < 8:
        return None
    mac = mac.lower().replace(":", "-")
    v = _OUI.get(mac[:8])
    if v:
        return v
    # Locally-administered bit set -> randomised MAC (modern phones do this)
    try:
        first = int(mac[:2], 16)
        if first & 0x02:
            return "randomised MAC (privacy)"
    except ValueError:
        pass
    return None
