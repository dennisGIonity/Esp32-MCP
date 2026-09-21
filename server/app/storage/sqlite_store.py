"""
SQLite implementation of Store.

Design notes for the 1000-device case:
  * WAL journal + NORMAL sync  -> concurrent readers while ingest writes
  * metrics stored as JSON in `telemetry`, PLUS a narrow `telemetry_metric`
    table (device_id, ts, name, value) so time-series queries stay indexed
    without a schema migration every time firmware adds a field
  * batched commits from the ingest queue, not one commit per reading
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from app.models import TelemetryIn, StatusIn, Alert
from app.storage.base import Store

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS devices (
    device_id   TEXT PRIMARY KEY,
    site        TEXT NOT NULL DEFAULT 'ionity-local',
    grp         TEXT NOT NULL DEFAULT 'default',
    label       TEXT,
    fw          TEXT,
    product     TEXT,
    ip          TEXT,
    transport   TEXT,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    msg_count   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS telemetry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL,
    site        TEXT,
    grp         TEXT,
    ts          REAL NOT NULL,
    uptime_s    INTEGER,
    seq         INTEGER,
    metrics     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tel_dev_ts ON telemetry(device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tel_ts     ON telemetry(ts DESC);

CREATE TABLE IF NOT EXISTS telemetry_metric (
    device_id   TEXT NOT NULL,
    ts          REAL NOT NULL,
    site        TEXT,
    grp         TEXT,
    name        TEXT NOT NULL,
    value       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tm_name_ts     ON telemetry_metric(name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_tm_dev_name_ts ON telemetry_metric(device_id, name, ts DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL,
    severity    TEXT NOT NULL,
    code        TEXT NOT NULL,
    message     TEXT NOT NULL,
    value       REAL,
    raised_at   REAL NOT NULL,
    cleared_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_alert_open ON alerts(cleared_at, raised_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_dev  ON alerts(device_id, raised_at DESC);

CREATE TABLE IF NOT EXISTS commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT NOT NULL,
    action      TEXT NOT NULL,
    payload     TEXT,
    issued_at   REAL NOT NULL
);
"""


def _numeric(v: Any) -> float | None:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return None


class SQLiteStore(Store):
    def __init__(self, path: str):
        self.path = path
        self.db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)
        await self.db.commit()

    async def close(self) -> None:
        if self.db:
            await self.db.commit()
            await self.db.close()

    # -- devices -----------------------------------------------------------
    async def upsert_device(self, t: TelemetryIn | StatusIn) -> None:
        now = time.time()
        ip = getattr(t, "ip", None) or getattr(getattr(t, "net", None), "ip", None)
        transport = getattr(t, "transport", None) or getattr(
            getattr(t, "net", None), "transport", None
        )
        await self.db.execute(
            """
            INSERT INTO devices (device_id, site, grp, label, fw, product, ip,
                                 transport, first_seen, last_seen, msg_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,1)
            ON CONFLICT(device_id) DO UPDATE SET
                site=excluded.site, grp=excluded.grp,
                label=COALESCE(excluded.label, devices.label),
                fw=COALESCE(excluded.fw, devices.fw),
                product=COALESCE(excluded.product, devices.product),
                ip=COALESCE(excluded.ip, devices.ip),
                transport=COALESCE(excluded.transport, devices.transport),
                last_seen=excluded.last_seen,
                msg_count=devices.msg_count+1
            """,
            (
                t.device_id, t.site, t.group, t.label, t.fw,
                getattr(t, "product", None), ip, transport, now, now,
            ),
        )

    async def list_devices(self, site=None, group=None) -> list[dict[str, Any]]:
        q = "SELECT * FROM devices WHERE 1=1"
        p: list[Any] = []
        if site:
            q += " AND site=?"; p.append(site)
        if group:
            q += " AND grp=?"; p.append(group)
        q += " ORDER BY device_id"
        async with self.db.execute(q, p) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # -- telemetry ---------------------------------------------------------
    async def insert_telemetry(self, t: TelemetryIn) -> None:
        ts = t.at()
        await self.db.execute(
            "INSERT INTO telemetry (device_id, site, grp, ts, uptime_s, seq, metrics)"
            " VALUES (?,?,?,?,?,?,?)",
            (t.device_id, t.site, t.group, ts, t.uptime_s, t.seq, json.dumps(t.metrics)),
        )
        rows = [
            (t.device_id, ts, t.site, t.group, k, n)
            for k, v in t.metrics.items()
            if (n := _numeric(v)) is not None
        ]
        if rows:
            await self.db.executemany(
                "INSERT INTO telemetry_metric (device_id, ts, site, grp, name, value)"
                " VALUES (?,?,?,?,?,?)",
                rows,
            )

    async def query_telemetry(
        self, device_id=None, site=None, group=None, metric=None,
        since_s=None, until_s=None, limit=500,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 50_000))
        if metric:
            q = "SELECT device_id, ts, name, value FROM telemetry_metric WHERE name=?"
            p: list[Any] = [metric]
        else:
            q = "SELECT device_id, ts, uptime_s, seq, metrics FROM telemetry WHERE 1=1"
            p = []
        if device_id:
            q += " AND device_id=?"; p.append(device_id)
        if site:
            q += " AND site=?"; p.append(site)
        if group:
            q += " AND grp=?"; p.append(group)
        if since_s:
            q += " AND ts>=?"; p.append(since_s)
        if until_s:
            q += " AND ts<=?"; p.append(until_s)
        q += " ORDER BY ts DESC LIMIT ?"; p.append(limit)

        async with self.db.execute(q, p) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        if not metric:
            for r in rows:
                r["metrics"] = json.loads(r["metrics"])
        return rows

    async def aggregate_metric(self, metric, since_s, site=None, group=None):
        q = (
            "SELECT COUNT(*) n, AVG(value) avg, MIN(value) min, MAX(value) max,"
            " COUNT(DISTINCT device_id) devices"
            " FROM telemetry_metric WHERE name=? AND ts>=?"
        )
        p: list[Any] = [metric, since_s]
        if site:
            q += " AND site=?"; p.append(site)
        if group:
            q += " AND grp=?"; p.append(group)
        async with self.db.execute(q, p) as cur:
            row = dict(await cur.fetchone())

        q2 = (
            "SELECT device_id, AVG(value) avg, MAX(value) max, MIN(value) min"
            " FROM telemetry_metric WHERE name=? AND ts>=?"
        )
        p2: list[Any] = [metric, since_s]
        if site:
            q2 += " AND site=?"; p2.append(site)
        if group:
            q2 += " AND grp=?"; p2.append(group)
        q2 += " GROUP BY device_id ORDER BY avg DESC LIMIT 10"
        async with self.db.execute(q2, p2) as cur:
            top = [dict(r) for r in await cur.fetchall()]

        return {"metric": metric, "window_start": since_s, **row, "top_devices": top}

    # -- alerts ------------------------------------------------------------
    async def raise_alert(self, alert: Alert) -> int:
        async with self.db.execute(
            "SELECT id FROM alerts WHERE device_id=? AND code=? AND cleared_at IS NULL",
            (alert.device_id, alert.code),
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            return int(existing["id"])
        cur = await self.db.execute(
            "INSERT INTO alerts (device_id, severity, code, message, value, raised_at)"
            " VALUES (?,?,?,?,?,?)",
            (alert.device_id, alert.severity, alert.code, alert.message,
             alert.value, alert.raised_at),
        )
        return int(cur.lastrowid)

    async def clear_alert(self, device_id: str, code: str, at: float) -> None:
        await self.db.execute(
            "UPDATE alerts SET cleared_at=? WHERE device_id=? AND code=? AND cleared_at IS NULL",
            (at, device_id, code),
        )

    async def list_alerts(self, device_id=None, open_only=True, limit=200):
        q = "SELECT * FROM alerts WHERE 1=1"
        p: list[Any] = []
        if open_only:
            q += " AND cleared_at IS NULL"
        if device_id:
            q += " AND device_id=?"; p.append(device_id)
        q += " ORDER BY raised_at DESC LIMIT ?"; p.append(int(limit))
        async with self.db.execute(q, p) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # -- commands / maintenance -------------------------------------------
    async def log_command(self, device_id: str, action: str, payload: str) -> int:
        cur = await self.db.execute(
            "INSERT INTO commands (device_id, action, payload, issued_at) VALUES (?,?,?,?)",
            (device_id, action, payload, time.time()),
        )
        await self.db.commit()
        return int(cur.lastrowid)

    async def prune(self, older_than_s: float) -> int:
        c1 = await self.db.execute("DELETE FROM telemetry WHERE ts < ?", (older_than_s,))
        c2 = await self.db.execute("DELETE FROM telemetry_metric WHERE ts < ?", (older_than_s,))
        await self.db.commit()
        return (c1.rowcount or 0) + (c2.rowcount or 0)

    async def commit(self) -> None:
        await self.db.commit()
