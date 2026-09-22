"""
AEDI - IONITY GLOBAL | DNS visibility storage

Mixed into SQLiteStore. Keeps the fleet tables and the DNS tables in one
database so the MCP layer can correlate "which device" with "what it asked
for" in a single query.

`blocked` is stored from day one even though nothing blocks yet -- that way
turning this into a filtering resolver later needs no migration.
"""
from __future__ import annotations

import time
from typing import Any

DNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS dns_queries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    client_ip   TEXT NOT NULL,
    qname       TEXT NOT NULL,
    qtype       TEXT NOT NULL,
    answers     TEXT,
    rcode       INTEGER DEFAULT 0,
    cached      INTEGER DEFAULT 0,
    blocked     INTEGER DEFAULT 0,
    latency_ms  REAL
);
CREATE INDEX IF NOT EXISTS idx_dns_ts        ON dns_queries(ts DESC);
CREATE INDEX IF NOT EXISTS idx_dns_client_ts ON dns_queries(client_ip, ts DESC);
CREATE INDEX IF NOT EXISTS idx_dns_name_ts   ON dns_queries(qname, ts DESC);

CREATE TABLE IF NOT EXISTS lan_devices (
    ip          TEXT PRIMARY KEY,
    mac         TEXT,
    hostname    TEXT,
    vendor      TEXT,
    label       TEXT,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    query_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_lan_last ON lan_devices(last_seen DESC);
"""


class DnsStoreMixin:
    DNS_SCHEMA = DNS_SCHEMA

    # -- writes ------------------------------------------------------------
    async def insert_dns_batch(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        await self.db.executemany(
            "INSERT INTO dns_queries (ts, client_ip, qname, qtype, answers,"
            " rcode, cached, blocked, latency_ms) VALUES (?,?,?,?,?,?,?,?,?)",
            [(r["ts"], r["client_ip"], r["qname"], r["qtype"],
              r.get("answers"), r.get("rcode", 0), int(r.get("cached", 0)),
              int(r.get("blocked", 0)), r.get("latency_ms")) for r in rows],
        )
        seen: dict[str, int] = {}
        for r in rows:
            seen[r["client_ip"]] = seen.get(r["client_ip"], 0) + 1
        now = time.time()
        await self.db.executemany(
            """
            INSERT INTO lan_devices (ip, first_seen, last_seen, query_count)
            VALUES (?,?,?,?)
            ON CONFLICT(ip) DO UPDATE SET
                last_seen=excluded.last_seen,
                query_count=lan_devices.query_count+excluded.query_count
            """,
            [(ip, now, now, n) for ip, n in seen.items()],
        )
        await self.db.commit()

    async def upsert_lan_device(self, ip: str, mac: str | None = None,
                                hostname: str | None = None,
                                vendor: str | None = None,
                                label: str | None = None) -> None:
        now = time.time()
        await self.db.execute(
            """
            INSERT INTO lan_devices (ip, mac, hostname, vendor, label, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(ip) DO UPDATE SET
                mac=COALESCE(excluded.mac, lan_devices.mac),
                hostname=COALESCE(excluded.hostname, lan_devices.hostname),
                vendor=COALESCE(excluded.vendor, lan_devices.vendor),
                label=COALESCE(excluded.label, lan_devices.label),
                last_seen=excluded.last_seen
            """,
            (ip, mac, hostname, vendor, label, now, now),
        )
        await self.db.commit()

    # -- reads -------------------------------------------------------------
    async def dns_recent(self, limit: int = 100, client_ip: str | None = None):
        q = ("SELECT d.ts, d.client_ip, d.qname, d.qtype, d.answers, d.cached,"
             " l.hostname, l.label, l.vendor"
             " FROM dns_queries d LEFT JOIN lan_devices l ON l.ip = d.client_ip"
             " WHERE 1=1")
        p: list[Any] = []
        if client_ip:
            q += " AND d.client_ip=?"; p.append(client_ip)
        q += " ORDER BY d.ts DESC LIMIT ?"; p.append(max(1, min(int(limit), 5000)))
        async with self.db.execute(q, p) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def dns_top_domains(self, since_s: float, limit: int = 25,
                              client_ip: str | None = None):
        q = ("SELECT qname, COUNT(*) hits, COUNT(DISTINCT client_ip) devices,"
             " MAX(ts) last_seen FROM dns_queries WHERE ts>=?")
        p: list[Any] = [since_s]
        if client_ip:
            q += " AND client_ip=?"; p.append(client_ip)
        q += " GROUP BY qname ORDER BY hits DESC LIMIT ?"
        p.append(max(1, min(int(limit), 500)))
        async with self.db.execute(q, p) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def dns_by_device(self, since_s: float, limit: int = 50):
        """Who is talking, how much, and what they asked for most."""
        async with self.db.execute(
            """
            SELECT d.client_ip,
                   COUNT(*)                  AS queries,
                   COUNT(DISTINCT d.qname)   AS distinct_domains,
                   MAX(d.ts)                 AS last_seen,
                   l.hostname, l.label, l.mac, l.vendor
            FROM dns_queries d
            LEFT JOIN lan_devices l ON l.ip = d.client_ip
            WHERE d.ts >= ?
            GROUP BY d.client_ip
            ORDER BY queries DESC
            LIMIT ?
            """,
            (since_s, max(1, min(int(limit), 500))),
        ) as cur:
            devices = [dict(r) for r in await cur.fetchall()]

        for dev in devices:
            async with self.db.execute(
                "SELECT qname, COUNT(*) hits FROM dns_queries"
                " WHERE client_ip=? AND ts>=? GROUP BY qname"
                " ORDER BY hits DESC LIMIT 5",
                (dev["client_ip"], since_s),
            ) as cur:
                dev["top_domains"] = [dict(r) for r in await cur.fetchall()]
        return devices

    async def dns_search(self, pattern: str, since_s: float, limit: int = 200):
        async with self.db.execute(
            "SELECT d.ts, d.client_ip, d.qname, d.qtype, d.answers, l.hostname, l.label"
            " FROM dns_queries d LEFT JOIN lan_devices l ON l.ip = d.client_ip"
            " WHERE d.qname LIKE ? AND d.ts >= ?"
            " ORDER BY d.ts DESC LIMIT ?",
            (f"%{pattern.lower()}%", since_s, max(1, min(int(limit), 2000))),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def dns_summary(self, since_s: float):
        async with self.db.execute(
            "SELECT COUNT(*) queries, COUNT(DISTINCT qname) domains,"
            " COUNT(DISTINCT client_ip) devices,"
            " SUM(cached) cache_hits, SUM(blocked) blocked,"
            " AVG(latency_ms) avg_latency_ms"
            " FROM dns_queries WHERE ts>=?",
            (since_s,),
        ) as cur:
            row = dict(await cur.fetchone())
        row["window_start"] = since_s
        return row

    async def list_lan_devices(self, limit: int = 200):
        async with self.db.execute(
            "SELECT * FROM lan_devices ORDER BY last_seen DESC LIMIT ?",
            (max(1, min(int(limit), 1000)),),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def prune_dns(self, older_than_s: float) -> int:
        cur = await self.db.execute("DELETE FROM dns_queries WHERE ts < ?", (older_than_s,))
        await self.db.commit()
        return cur.rowcount or 0
