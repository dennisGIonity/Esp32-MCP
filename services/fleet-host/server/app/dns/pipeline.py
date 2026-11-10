"""
AEDI - IONITY GLOBAL | LAN DNS pipeline
Doc ID: DOC-2026-10-ESP32MCP-DNSPIPE | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Every DNS query the fleet sees - from an ESP32 or Pi node's forwarder
(EWP/1 DNS_BATCH / DNS_FLAGGED) or from the host's own resolver - goes
through here exactly once:

    record ──► attribute (MAC, host name, device_labels.json label)
           ──► rule engine (category, ALERT?)  ──► flag events + fleet alert + alarm light
           ──► live subscribers (WebSocket / MCP resource updates)
           ──► batched writer (one transaction per second) ──► dns_queries / lan_devices

ingest() never awaits: it runs on the event loop from the MQTT bridge and the
resolver, does in-memory work only and queues the durable write.

Double counting: a node publishes DNS_FLAGGED the moment a client hits an
ALERT rule and the same query again inside its next DNS_BATCH. The immediate
event opens the flag with 1 hit and leaves a credit; the batch entry spends
it, so "YouTube, 41 hits" means 41 queries, not 42.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

log = logging.getLogger("ionity.dnspipe")

CREDIT_TTL_S = 180.0
SUBSCRIBER_QUEUE = 2000


@dataclass
class DnsRecord:
    ts: float
    client_ip: str
    qname: str
    qtype: int | str = 1
    rcode: int = 0
    latency_ms: float | None = None
    count: int = 1
    cached: bool = False
    upstream_failed: bool = False
    mac: str | None = None
    hostname: str | None = None
    node_id: str | None = None
    answers: str | None = None


@dataclass
class _Credit:
    n: int
    at: float = field(default_factory=time.monotonic)


class DnsPipeline:
    def __init__(self, store, rules, labels, settings):
        self.store = store
        self.rules = rules
        self.labels = labels
        self.s = settings
        self._rows: list[dict[str, Any]] = []
        self._writer: asyncio.Task | None = None
        self._pruner: asyncio.Task | None = None
        self._busy: set[asyncio.Task] = set()
        self._credits: dict[tuple[str, str], _Credit] = {}
        self._rule_hits: dict[int, int] = {}
        self._subs: set[asyncio.Queue] = set()
        self.flag_listeners: list[Callable[[dict, bool], Any]] = []
        self.stats = {"ingested": 0, "flagged": 0, "flag_events": 0, "written": 0, "dropped_live": 0,
                      "write_errors": 0}
        self._stopping = False
        self._flag_lock = asyncio.Lock()      # (client, rule) folding is read-then-write: serialise it

    # -- lifecycle -------------------------------------------------------------
    async def start(self) -> None:
        self._writer = asyncio.create_task(self._writer_loop(), name="dns-writer")
        self._pruner = asyncio.create_task(self._pruner_loop(), name="dns-pruner")

    async def stop(self) -> None:
        self._stopping = True
        if self._busy:
            await asyncio.gather(*list(self._busy), return_exceptions=True)
        if self._writer:
            try:
                await asyncio.wait_for(self._writer, 10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._writer.cancel()
        if self._pruner:
            self._pruner.cancel()
            try:
                await self._pruner
            except asyncio.CancelledError:
                pass

    # -- live subscribers ------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, msg: dict[str, Any]) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                self.stats["dropped_live"] += 1       # a slow viewer never slows ingest

    # -- ingest -----------------------------------------------------------------------
    def attribute(self, client_ip: str, mac: str | None, hostname: str | None) -> tuple[str | None, str]:
        return self.labels.resolve(mac=mac, ip=client_ip, hostname=hostname)

    def ingest(self, records: list[DnsRecord]) -> int:
        n = 0
        now = time.monotonic()
        for k in [k for k, c in self._credits.items() if now - c.at > CREDIT_TTL_S]:
            del self._credits[k]
        for r in records:
            qname = (r.qname or "").lower().rstrip(".")[:253]
            if not qname or not r.client_ip:
                continue
            label, _src = self.attribute(r.client_ip, r.mac, r.hostname)
            m = self.rules.match(qname)
            row = {"ts": r.ts, "client_ip": r.client_ip, "mac": r.mac, "hostname": r.hostname or None,
                   "label": label, "node_id": r.node_id, "qname": qname, "qtype": r.qtype, "rcode": r.rcode,
                   "cached": r.cached, "latency_ms": r.latency_ms, "count": max(1, int(r.count)),
                   "answers": r.answers, "category": m.category.name if m else None,
                   "flagged": bool(m), "rule_id": m.rule.id if m else None,
                   "rule": m.rule.pattern if m else None, "upstream_failed": r.upstream_failed}
            self._rows.append(row)
            n += 1
            if m:
                self.stats["flagged"] += row["count"]
                self._rule_hits[m.rule.id] = self._rule_hits.get(m.rule.id, 0) + row["count"]
                if m.rule.alert:
                    key = (r.client_ip, m.rule.pattern)
                    credit = self._credits.get(key)
                    hits = row["count"]
                    if credit:
                        spent = min(credit.n, hits)
                        credit.n -= spent
                        hits -= spent
                        if credit.n <= 0:
                            del self._credits[key]
                    if hits > 0:
                        self._spawn(self._flag(row, hits))
            self.publish({"type": "dns", "query": _public(row)})
        self.stats["ingested"] += n
        return n

    def flag_now(self, r: DnsRecord) -> bool:
        """A node's immediate DNS_FLAGGED. The host re-checks the name against ITS rules
        (authoritative); a stale node rule that the host no longer has is ignored."""
        qname = (r.qname or "").lower().rstrip(".")
        m = self.rules.match(qname)
        if not m or not m.rule.alert:
            log.info("node %s flagged %s but no host ALERT rule matches - ignored (watchlist out of sync?)",
                     r.node_id, qname)
            return False
        label, _src = self.attribute(r.client_ip, r.mac, r.hostname)
        row = {"ts": r.ts, "client_ip": r.client_ip, "mac": r.mac, "hostname": r.hostname or None,
               "label": label, "node_id": r.node_id, "qname": qname, "qtype": r.qtype,
               "category": m.category.name, "rule": m.rule.pattern, "rule_id": m.rule.id, "count": 1,
               "flagged": True}
        key = (r.client_ip, m.rule.pattern)
        c = self._credits.setdefault(key, _Credit(0))
        c.n += 1
        c.at = time.monotonic()
        self._spawn(self._flag(row, 1))
        return True

    async def _flag(self, row: dict[str, Any], hits: int) -> None:
        try:
            async with self._flag_lock:
                eid, is_new = await self.store.record_flag(
                    ts=row["ts"], client_ip=row["client_ip"], mac=row.get("mac"), hostname=row.get("hostname"),
                    label=row.get("label"), node_id=row.get("node_id"), rule=row["rule"],
                    category=row["category"], qname=row["qname"], hits=hits, hold_s=float(self.s.watch_hold_s))
        except Exception:                                   # noqa: BLE001
            log.exception("could not record a flag event")
            return
        self.stats["flag_events"] += int(is_new)
        ev = {"id": eid, "new": is_new, "ts": row["ts"], "client_ip": row["client_ip"], "mac": row.get("mac"),
              "hostname": row.get("hostname"), "label": row.get("label"), "node_id": row.get("node_id"),
              "qname": row["qname"], "rule": row["rule"], "category": row["category"], "hits": hits}
        self.publish({"type": "flag", "flag": ev})
        for fn in list(self.flag_listeners):
            try:
                res = fn(ev, is_new)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:                               # noqa: BLE001
                log.exception("flag listener failed")

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._busy.add(t)
        t.add_done_callback(self._busy.discard)

    # -- durable writes -----------------------------------------------------------------
    async def flush(self) -> int:
        if not self._rows:
            return 0
        batch, self._rows = self._rows, []
        hits, self._rule_hits = self._rule_hits, {}
        try:
            await self.store.insert_dns_rows(batch)
            if hits:
                await self.rules.count_hits(hits)
            self.stats["written"] += len(batch)
        except Exception:                                   # noqa: BLE001
            self.stats["write_errors"] += 1
            log.exception("DNS batch write failed (%d rows kept for the next try)", len(batch))
            if len(self._rows) + len(batch) <= 100_000:
                self._rows[:0] = batch
        return len(batch)

    async def _writer_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(1.0)
                await self.flush()
                if self._stopping:
                    return
            except asyncio.CancelledError:
                await self.flush()
                raise

    async def _pruner_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(3600)
                cutoff = time.time() - self.s.dns_retention_days * 86400
                n = await self.store.prune_dns(cutoff, self.s.dns_max_rows or None)
                if n:
                    log.info("DNS retention: pruned %d rows (%d days / %d rows max)", n,
                             self.s.dns_retention_days, self.s.dns_max_rows)
                    await self.store.checkpoint()
            except asyncio.CancelledError:
                break
            except Exception:                               # noqa: BLE001
                log.exception("DNS pruner error")


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row.get(k) for k in ("ts", "client_ip", "mac", "hostname", "label", "node_id", "qname", "qtype",
                                     "rcode", "cached", "latency_ms", "count", "category", "flagged", "rule")}
