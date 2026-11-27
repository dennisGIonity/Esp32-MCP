"""
AEDI - IONITY GLOBAL | LAN DNS + watchlist storage
Doc ID: DOC-2026-10-ESP32MCP-DNSSTORE | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Mixed into SQLiteStore: the fleet tables and the DNS tables share one WAL
database, so "which device" and "what it asked for" join in one query.

  dns_queries   one row per (folded) query: who (ip, mac, host name, label),
                what (qname, type, rcode, latency, count), seen by which node,
                matched which watchlist rule / category
  lan_devices   last known identity per client address
  flag_events   watchlist ALERT hits, folded per (client, category) while active,
                with a hit count - "who opened YouTube in the last 30 minutes"
  watch_categories / watch_rules   the rule engine's persistent state

Writes are batched: one executemany per table, one commit per batch.
Retention: rows older than dns_retention_days AND beyond dns_max_rows go.
"""
from __future__ import annotations

import time
from typing import Any
from collections.abc import Iterable

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

CREATE TABLE IF NOT EXISTS watch_categories (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    label    TEXT,
    builtin  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS watch_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern         TEXT NOT NULL UNIQUE,
    suffix          TEXT NOT NULL,
    subdomains_only INTEGER NOT NULL DEFAULT 0,
    category_id     INTEGER NOT NULL REFERENCES watch_categories(id),
    alert           INTEGER NOT NULL DEFAULT 1,
    enabled         INTEGER NOT NULL DEFAULT 1,
    builtin         INTEGER NOT NULL DEFAULT 0,
    created_at      REAL,
    hits            INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS flag_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    first_ts    REAL NOT NULL,
    last_ts     REAL NOT NULL,
    client_ip   TEXT NOT NULL,
    mac         TEXT,
    hostname    TEXT,
    label       TEXT,
    node_id     TEXT,
    rule        TEXT NOT NULL,
    category    TEXT NOT NULL,
    last_qname  TEXT NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_flag_last   ON flag_events(last_ts DESC);
CREATE INDEX IF NOT EXISTS idx_flag_client ON flag_events(client_ip, category, last_ts DESC);
"""

# Columns added after v2.2 - created by migrate_dns() on old databases.
_DNS_COLUMNS = {
    "mac": "TEXT", "hostname": "TEXT", "label": "TEXT", "node_id": "TEXT",
    "count": "INTEGER NOT NULL DEFAULT 1", "category": "TEXT", "flagged": "INTEGER NOT NULL DEFAULT 0",
    "rule_id": "INTEGER",
}
_LAN_COLUMNS = {"last_dns": "REAL", "node_id": "TEXT", "sources": "TEXT"}
_DNS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_dns_flag_ts ON dns_queries(flagged, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dns_mac_ts  ON dns_queries(mac, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dns_cat_ts  ON dns_queries(category, ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dns_label_ts ON dns_queries(label, ts DESC)",
)

_QTYPE = {1: "A", 2: "NS", 5: "CNAME", 6: "SOA", 12: "PTR", 15: "MX", 16: "TXT", 28: "AAAA",
          33: "SRV", 64: "SVCB", 65: "HTTPS"}


def qtype_name(q: int | str) -> str:
    return q if isinstance(q, str) else _QTYPE.get(q, str(q))


def like_pattern(domain_pattern: str) -> tuple[str, str | None]:
    """'*.youtube.com' -> suffix match; 'youtube' -> substring; 'youtube.com' -> name or subdomain.
    Returns (SQL condition on qname, LIKE argument with '\\' escaping)."""
    p = domain_pattern.strip().lower().rstrip(".")
    esc = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if p.startswith("*."):
        return "qname LIKE ? ESCAPE '\\'", "%." + esc[2:]
    if "." in p:
        return "(qname = ? OR qname LIKE ? ESCAPE '\\')", esc
    return "qname LIKE ? ESCAPE '\\'", f"%{esc}%"


class DnsStoreMixin:
    DNS_SCHEMA = DNS_SCHEMA

    async def migrate_dns(self) -> None:
        for table, cols in (("dns_queries", _DNS_COLUMNS), ("lan_devices", _LAN_COLUMNS)):
            async with self.db.execute(f"PRAGMA table_info({table})") as cur:
                have = {r[1] for r in await cur.fetchall()}
            for col, decl in cols.items():
                if col not in have:
                    await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    if table == "lan_devices" and col == "last_dns":
                        await self.db.execute(
                            "UPDATE lan_devices SET last_dns = (SELECT MAX(ts) FROM dns_queries q"
                            " WHERE q.client_ip = lan_devices.ip)")
        for ddl in _DNS_INDEXES:
            await self.db.execute(ddl)

    # -- DNS writes ----------------------------------------------------------
    async def insert_dns_rows(self, rows: list[dict[str, Any]]) -> None:
        """One transaction: dns_queries + lan_devices for the whole batch."""
        if not rows:
            return
        await self.db.executemany(
            "INSERT INTO dns_queries (ts, client_ip, mac, hostname, label, node_id, qname, qtype, answers,"
            " rcode, cached, blocked, latency_ms, count, category, flagged, rule_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["ts"], r["client_ip"], r.get("mac"), r.get("hostname"), r.get("label"), r.get("node_id"),
              r["qname"], qtype_name(r.get("qtype", 1)), r.get("answers"), r.get("rcode", 0),
              int(bool(r.get("cached"))), int(bool(r.get("blocked"))), r.get("latency_ms"),
              int(r.get("count") or 1), r.get("category"), int(bool(r.get("flagged"))), r.get("rule_id"))
             for r in rows])
        per_ip: dict[str, dict[str, Any]] = {}
        for r in rows:
            d = per_ip.setdefault(r["client_ip"], {"n": 0, "ts": 0.0, "mac": None, "hostname": None,
                                                   "label": None, "node_id": None})
            d["n"] += int(r.get("count") or 1)
            if r["ts"] >= d["ts"]:
                d["ts"] = r["ts"]
            for k in ("mac", "hostname", "label", "node_id"):
                d[k] = r.get(k) or d[k]
        await self.db.executemany(
            """
            INSERT INTO lan_devices (ip, mac, hostname, label, node_id, first_seen, last_seen, last_dns, query_count)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ip) DO UPDATE SET
                mac=COALESCE(excluded.mac, lan_devices.mac),
                hostname=COALESCE(excluded.hostname, lan_devices.hostname),
                label=COALESCE(excluded.label, lan_devices.label),
                node_id=COALESCE(excluded.node_id, lan_devices.node_id),
                last_seen=MAX(excluded.last_seen, lan_devices.last_seen),
                last_dns=MAX(excluded.last_dns, COALESCE(lan_devices.last_dns, 0)),
                query_count=lan_devices.query_count+excluded.query_count
            """,
            [(ip, d["mac"], d["hostname"], d["label"], d["node_id"], d["ts"], d["ts"], d["ts"], d["n"])
             for ip, d in per_ip.items()])
        await self.db.commit()

    async def insert_dns_batch(self, rows: list[dict[str, Any]]) -> None:
        """v2.2 name, kept for callers that write unenriched rows."""
        await self.insert_dns_rows(rows)

    async def upsert_lan_device(self, ip: str, mac: str | None = None, hostname: str | None = None,
                                vendor: str | None = None, label: str | None = None,
                                node_id: str | None = None, sources: str | None = None) -> None:
        now = time.time()
        await self.db.execute(
            """
            INSERT INTO lan_devices (ip, mac, hostname, vendor, label, node_id, sources, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ip) DO UPDATE SET
                mac=COALESCE(excluded.mac, lan_devices.mac),
                hostname=COALESCE(excluded.hostname, lan_devices.hostname),
                vendor=COALESCE(excluded.vendor, lan_devices.vendor),
                label=COALESCE(excluded.label, lan_devices.label),
                node_id=COALESCE(excluded.node_id, lan_devices.node_id),
                sources=COALESCE(excluded.sources, lan_devices.sources),
                last_seen=excluded.last_seen
            """,
            (ip, mac, hostname, vendor, label, node_id, sources, now, now),
        )
        await self.db.commit()

    async def upsert_lan_devices(self, rows: Iterable[dict[str, Any]]) -> None:
        rows = list(rows)
        if not rows:
            return
        await self.db.executemany(
            """
            INSERT INTO lan_devices (ip, mac, hostname, vendor, label, node_id, sources, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ip) DO UPDATE SET
                mac=COALESCE(excluded.mac, lan_devices.mac),
                hostname=COALESCE(excluded.hostname, lan_devices.hostname),
                vendor=COALESCE(excluded.vendor, lan_devices.vendor),
                label=COALESCE(excluded.label, lan_devices.label),
                node_id=COALESCE(excluded.node_id, lan_devices.node_id),
                sources=COALESCE(excluded.sources, lan_devices.sources),
                last_seen=MAX(excluded.last_seen, lan_devices.last_seen)
            """,
            [(r["ip"], r.get("mac"), r.get("hostname"), r.get("vendor"), r.get("label"), r.get("node_id"),
              r.get("sources"), r["last_seen"], r["last_seen"]) for r in rows])
        await self.db.commit()

    # -- flag events --------------------------------------------------------------
    async def record_flag(self, *, ts: float, client_ip: str, mac: str | None, hostname: str | None,
                          label: str | None, node_id: str | None, rule: str, category: str,
                          qname: str, hits: int, hold_s: float) -> tuple[int, bool]:
        """Fold into the open episode for (client, category) - youtube.com, *.googlevideo.com and
        *.ytimg.com are ONE YouTube session - if it was active within hold_s, else open a new
        one. -> (event id, is_new)."""
        async with self.db.execute(
            "SELECT id FROM flag_events WHERE client_ip=? AND category=? AND last_ts>=? ORDER BY last_ts DESC LIMIT 1",
            (client_ip, category, ts - hold_s)) as cur:
            row = await cur.fetchone()
        if row:
            await self.db.execute(
                "UPDATE flag_events SET last_ts=MAX(last_ts, ?), hits=hits+?, last_qname=?, rule=?,"
                " mac=COALESCE(?, mac), hostname=COALESCE(?, hostname), label=COALESCE(?, label),"
                " node_id=COALESCE(?, node_id) WHERE id=?",
                (ts, hits, qname, rule, mac, hostname, label, node_id, row[0]))
            await self.db.commit()
            return row[0], False
        cur = await self.db.execute(
            "INSERT INTO flag_events (first_ts, last_ts, client_ip, mac, hostname, label, node_id, rule, category,"
            " last_qname, hits) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts, ts, client_ip, mac, hostname, label, node_id, rule, category, qname, hits))
        await self.db.commit()
        return cur.lastrowid, True

    async def list_flags(self, *, since: float | None = None, until: float | None = None,
                         category: str | None = None, client_ip: str | None = None, mac: str | None = None,
                         label: str | None = None, active_within_s: float | None = None,
                         limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        q = "SELECT * FROM flag_events WHERE 1=1"
        p: list[Any] = []
        if since is not None:
            q += " AND last_ts>=?"; p.append(since)
        if until is not None:
            q += " AND first_ts<=?"; p.append(until)
        if active_within_s is not None:
            q += " AND last_ts>=?"; p.append(time.time() - active_within_s)
        if category:
            q += " AND (category=? OR category LIKE ?)"; p += [category, f"%:{category}"]
        if client_ip:
            q += " AND client_ip=?"; p.append(client_ip)
        if mac:
            q += " AND mac=?"; p.append(mac)
        if label:
            q += " AND label LIKE ?"; p.append(f"%{label}%")
        q += " ORDER BY last_ts DESC LIMIT ? OFFSET ?"
        p += [max(1, min(int(limit), 1000)), max(0, int(offset))]
        async with self.db.execute(q, p) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # -- DNS reads -------------------------------------------------------------------
    async def query_dns(self, *, domain_pattern: str | None = None, client_ip: str | None = None,
                        mac: str | None = None, label: str | None = None, category: str | None = None,
                        node_id: str | None = None, flagged_only: bool = False, since: float | None = None,
                        until: float | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        q = ("SELECT id, ts, client_ip, mac, hostname, label, node_id, qname, qtype, rcode, cached,"
             " latency_ms, count, category, flagged FROM dns_queries WHERE 1=1")
        p: list[Any] = []
        if domain_pattern:
            cond, arg = like_pattern(domain_pattern)
            q += f" AND {cond}"
            if cond.startswith("(qname = ?"):
                p += [arg, "%." + arg]
            else:
                p.append(arg)
        if client_ip:
            q += " AND client_ip=?"; p.append(client_ip)
        if mac:
            q += " AND mac=?"; p.append(mac.lower().replace(":", "-"))
        if label:
            q += " AND label LIKE ?"; p.append(f"%{label}%")
        if category:
            q += " AND (category=? OR category LIKE ?)"; p += [category, f"%:{category}"]
        if node_id:
            q += " AND node_id=?"; p.append(node_id)
        if flagged_only:
            q += " AND flagged=1"
        if since is not None:
            q += " AND ts>=?"; p.append(since)
        if until is not None:
            q += " AND ts<=?"; p.append(until)
        lim = max(1, min(int(limit), 1000))
        q += " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"
        p += [lim + 1, max(0, int(offset))]
        async with self.db.execute(q, p) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        more = len(rows) > lim
        return {"queries": rows[:lim], "offset": int(offset), "limit": lim, "has_more": more,
                "next_offset": int(offset) + lim if more else None}

    async def dns_recent(self, limit: int = 100, client_ip: str | None = None):
        q = ("SELECT d.ts, d.client_ip, d.qname, d.qtype, d.answers, d.cached, d.count, d.flagged, d.category,"
             " d.node_id, COALESCE(d.mac, l.mac) mac, COALESCE(d.hostname, l.hostname) hostname,"
             " COALESCE(d.label, l.label) label, l.vendor"
             " FROM dns_queries d LEFT JOIN lan_devices l ON l.ip = d.client_ip WHERE 1=1")
        p: list[Any] = []
        if client_ip:
            q += " AND d.client_ip=?"; p.append(client_ip)
        q += " ORDER BY d.ts DESC LIMIT ?"; p.append(max(1, min(int(limit), 5000)))
        async with self.db.execute(q, p) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def dns_top_domains(self, since_s: float, limit: int = 25, client_ip: str | None = None):
        q = ("SELECT qname, SUM(count) hits, COUNT(DISTINCT client_ip) devices, MAX(ts) last_seen,"
             " MAX(category) category FROM dns_queries WHERE ts>=?")
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
                   SUM(d.count)              AS queries,
                   COUNT(DISTINCT d.qname)   AS distinct_domains,
                   SUM(d.flagged * d.count)  AS flagged,
                   MAX(d.ts)                 AS last_seen,
                   COALESCE(MAX(d.label), l.label) AS label, l.hostname, l.mac, l.vendor
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
                "SELECT qname, SUM(count) hits FROM dns_queries WHERE client_ip=? AND ts>=?"
                " GROUP BY qname ORDER BY hits DESC LIMIT 5", (dev["client_ip"], since_s)) as cur:
                dev["top_domains"] = [dict(r) for r in await cur.fetchall()]
        return devices

    async def dns_search(self, pattern: str, since_s: float, limit: int = 200):
        cond, arg = like_pattern(pattern)
        args: list[Any] = [arg, "%." + arg] if cond.startswith("(qname = ?") else [arg]
        q = ("SELECT d.ts, d.client_ip, d.qname, d.qtype, d.answers, d.count, d.flagged, d.category,"
             " COALESCE(d.mac, l.mac) mac, COALESCE(d.hostname, l.hostname) hostname,"
             " COALESCE(d.label, l.label) label FROM dns_queries d LEFT JOIN lan_devices l ON l.ip = d.client_ip"
             f" WHERE {cond.replace('qname', 'd.qname')} AND d.ts >= ? ORDER BY d.ts DESC LIMIT ?")
        async with self.db.execute(q, (*args, since_s, max(1, min(int(limit), 2000)))) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def dns_summary(self, since_s: float):
        async with self.db.execute(
            "SELECT COALESCE(SUM(count),0) queries, COUNT(DISTINCT qname) domains,"
            " COUNT(DISTINCT client_ip) devices, COALESCE(SUM(cached*count),0) cache_hits,"
            " COALESCE(SUM(blocked),0) blocked, COALESCE(SUM(flagged*count),0) flagged,"
            " COUNT(DISTINCT node_id) nodes, AVG(NULLIF(latency_ms,0)) avg_latency_ms"
            " FROM dns_queries WHERE ts>=?", (since_s,)) as cur:
            row = dict(await cur.fetchone())
        async with self.db.execute(
            "SELECT category, SUM(count) hits FROM dns_queries WHERE ts>=? AND category IS NOT NULL"
            " GROUP BY category ORDER BY hits DESC", (since_s,)) as cur:
            row["categories"] = [dict(r) for r in await cur.fetchall()]
        row["window_start"] = since_s
        return row

    async def list_lan_devices(self, limit: int = 200):
        async with self.db.execute("SELECT * FROM lan_devices ORDER BY last_seen DESC LIMIT ?",
                                   (max(1, min(int(limit), 1000)),)) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def lan_device(self, ip: str | None = None, mac: str | None = None) -> dict | None:
        if ip:
            q, p = "SELECT * FROM lan_devices WHERE ip=?", (ip,)
        elif mac:
            q, p = "SELECT * FROM lan_devices WHERE mac=? ORDER BY last_seen DESC LIMIT 1", (mac,)
        else:
            return None
        async with self.db.execute(q, p) as cur:
            r = await cur.fetchone()
        return dict(r) if r else None

    async def network_overview(self, since_s: float, limit: int = 300):
        """Every device on the LAN, with how much of its traffic we can see in the window."""
        async with self.db.execute(
            """
            SELECT l.*,
                   (SELECT COALESCE(SUM(q.count),0) FROM dns_queries q
                     WHERE q.client_ip = l.ip AND q.ts >= ?)          AS lookups,
                   (SELECT COUNT(DISTINCT q.qname) FROM dns_queries q
                     WHERE q.client_ip = l.ip AND q.ts >= ?)          AS domains
            FROM lan_devices l
            WHERE l.ip <> '127.0.0.1'
            ORDER BY lookups DESC, l.last_seen DESC
            LIMIT ?
            """,
            (since_s, since_s, max(1, min(int(limit), 1000))),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        for d in rows:
            if d["lookups"]:
                async with self.db.execute(
                    "SELECT qname, SUM(count) hits, MAX(category) category FROM dns_queries"
                    " WHERE client_ip=? AND ts>=? GROUP BY qname ORDER BY hits DESC LIMIT 6",
                    (d["ip"], since_s)) as cur:
                    d["top_domains"] = [dict(r) for r in await cur.fetchall()]
            else:
                d["top_domains"] = []
        return rows

    # -- retention -------------------------------------------------------------------------
    async def prune_lan(self, older_than_s: float) -> int:
        cur = await self.db.execute("DELETE FROM lan_devices WHERE last_seen < ? AND ip <> '127.0.0.1'",
                                    (older_than_s,))
        await self.db.commit()
        return cur.rowcount or 0

    async def prune_dns(self, older_than_s: float, max_rows: int | None = None) -> int:
        n = 0
        cur = await self.db.execute("DELETE FROM dns_queries WHERE ts < ?", (older_than_s,))
        n += cur.rowcount or 0
        if max_rows:
            cur = await self.db.execute(
                "DELETE FROM dns_queries WHERE id <= (SELECT id FROM dns_queries ORDER BY id DESC LIMIT 1 OFFSET ?)",
                (int(max_rows),))
            n += cur.rowcount or 0
        cur = await self.db.execute("DELETE FROM flag_events WHERE last_ts < ?", (older_than_s,))
        n += cur.rowcount or 0
        await self.db.commit()
        return n

    # -- watchlist persistence ---------------------------------------------------------------
    async def seed_watchlist(self, categories: list[dict], rules: list[dict]) -> None:
        """Built-ins from packages/protocol: inserted once; a later release's new built-ins
        are added; a user's disable / category choice is never overwritten."""
        await self.db.executemany(
            "INSERT INTO watch_categories (id, name, label, builtin) VALUES (?,?,?,1)"
            " ON CONFLICT(id) DO UPDATE SET builtin=1",
            [(c["id"], c["name"], c["label"]) for c in categories])
        await self.db.executemany(
            "INSERT OR IGNORE INTO watch_rules (pattern, suffix, subdomains_only, category_id, alert, enabled,"
            " builtin, created_at) VALUES (?,?,?,?,?,1,1,?)",
            [(r["pattern"], r["suffix"], int(r["sub"]), r["category"], int(r["alert"]), time.time())
             for r in rules])
        await self.db.commit()

    async def list_watchlist(self) -> tuple[list[dict], list[dict]]:
        async with self.db.execute("SELECT * FROM watch_categories ORDER BY id") as cur:
            cats = [dict(r) for r in await cur.fetchall()]
        async with self.db.execute("SELECT * FROM watch_rules ORDER BY id") as cur:
            rules = [dict(r) for r in await cur.fetchall()]
        return cats, rules

    async def insert_category(self, cid: int, name: str, label: str) -> None:
        await self.db.execute("INSERT INTO watch_categories (id, name, label, builtin) VALUES (?,?,?,0)",
                              (cid, name, label))
        await self.db.commit()

    async def delete_category(self, cid: int) -> None:
        await self.db.execute("DELETE FROM watch_categories WHERE id=? AND builtin=0", (cid,))
        await self.db.commit()

    async def insert_rule(self, pattern: str, suffix: str, sub: bool, category_id: int, alert: bool) -> int:
        cur = await self.db.execute(
            "INSERT INTO watch_rules (pattern, suffix, subdomains_only, category_id, alert, enabled, builtin,"
            " created_at) VALUES (?,?,?,?,?,1,0,?)", (pattern, suffix, int(sub), category_id, int(alert), time.time()))
        await self.db.commit()
        return cur.lastrowid

    async def update_rule(self, rid: int, **fields: Any) -> None:
        allowed = {"category_id", "alert", "enabled"}
        sets = [(k, v) for k, v in fields.items() if k in allowed]
        if not sets:
            return
        await self.db.execute(f"UPDATE watch_rules SET {', '.join(f'{k}=?' for k, _ in sets)} WHERE id=?",
                              (*[v for _, v in sets], rid))
        await self.db.commit()

    async def delete_rule(self, rid: int) -> None:
        await self.db.execute("DELETE FROM watch_rules WHERE id=? AND builtin=0", (rid,))
        await self.db.commit()

    async def add_rule_hits(self, counts: dict[int, int]) -> None:
        await self.db.executemany("UPDATE watch_rules SET hits=hits+? WHERE id=?",
                                  [(n, rid) for rid, n in counts.items()])
        await self.db.commit()
