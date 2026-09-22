#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Fleet data purge
Doc ID: DOC-2026-09-ESP32MCP-PURGE | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

Removes devices and all their telemetry, metrics and alerts from the fleet
database. Written for clearing simulator output so only real hardware remains,
but it is a general tool.

DEFAULT IS A DRY RUN. Nothing is deleted unless you pass --commit.

    python scripts/purge_devices.py                      # preview simulated
    python scripts/purge_devices.py --commit             # do it
    python scripts/purge_devices.py --all --commit       # wipe every device
    python scripts/purge_devices.py --stale-minutes 60 --commit
    python scripts/purge_devices.py --keep esp32-98a316e5d18c --all --commit
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "fleet.db"

# The simulator generates ids as f"esp32-{i:012x}", so every one of them
# begins esp32-00000000. A real eFuse MAC never will.
SIM_PATTERN = "esp32-00000000%"


def main() -> int:
    p = argparse.ArgumentParser(description="Purge devices from the Ionity fleet DB")
    # argparse %-expands help text, so a literal % must be doubled.
    p.add_argument("--pattern", default=SIM_PATTERN,
                   help="SQL LIKE pattern for device_id "
                        "(default: " + SIM_PATTERN.replace("%", "%%") + ")")
    p.add_argument("--all", action="store_true",
                   help="target every device, not just the pattern")
    p.add_argument("--stale-minutes", type=float, default=None,
                   help="only devices not seen for this many minutes")
    p.add_argument("--keep", action="append", default=[],
                   help="device_id to preserve (repeatable)")
    p.add_argument("--commit", action="store_true",
                   help="actually delete; without this it is a dry run")
    p.add_argument("--db", default=str(DB))
    args = p.parse_args()

    if not Path(args.db).exists():
        print(f"database not found: {args.db}")
        return 1

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row

    where, params = [], []
    if not args.all:
        where.append("device_id LIKE ?")
        params.append(args.pattern)
    if args.stale_minutes is not None:
        where.append("last_seen < ?")
        params.append(time.time() - args.stale_minutes * 60)
    for k in args.keep:
        where.append("device_id <> ?")
        params.append(k)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    targets = [r["device_id"] for r in
               db.execute(f"SELECT device_id FROM devices{clause}", params)]
    survivors = [dict(r) for r in db.execute(
        "SELECT device_id, site, grp, last_seen, msg_count FROM devices"
        + (f" WHERE device_id NOT IN ({','.join('?' * len(targets))})" if targets else ""),
        targets)]

    print(f"database     : {args.db}")
    print(f"matching     : {len(targets)} device(s) to remove")
    if targets:
        show = targets[:5]
        print("   e.g.      : " + ", ".join(show) + (" ..." if len(targets) > 5 else ""))

    print(f"surviving    : {len(survivors)} device(s)")
    now = time.time()
    for s in survivors[:20]:
        age = now - (s["last_seen"] or 0)
        print(f"   KEEP      : {s['device_id']:<24} {s['site']}/{s['grp']:<16} "
              f"last seen {age:,.0f}s ago, {s['msg_count']} msgs")

    if not targets:
        print("\nnothing to do.")
        return 0

    if not args.commit:
        print("\nDRY RUN - nothing deleted. Re-run with --commit to apply.")
        return 0

    marks = ",".join("?" * len(targets))
    counts = {}
    for table, col in (("telemetry", "device_id"), ("telemetry_metric", "device_id"),
                       ("alerts", "device_id"), ("commands", "device_id"),
                       ("devices", "device_id")):
        cur = db.execute(f"DELETE FROM {table} WHERE {col} IN ({marks})", targets)
        counts[table] = cur.rowcount
    db.commit()
    db.execute("VACUUM")
    db.close()

    print("\ndeleted:")
    for t, n in counts.items():
        print(f"   {t:<20} {n:>8,} rows")
    print("\nRestart the fleet server so its in-memory registry reloads:")
    print("   the dashboard will then show only what is really reporting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
