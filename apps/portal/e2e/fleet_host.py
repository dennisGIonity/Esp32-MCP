#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | @ionity/portal e2e | real fleet host + simulated EWP/1 node
Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Starts the REAL fleet host (services/fleet-host/server, uvicorn, temp SQLite),
which serves the built portal at /portal/, and pairs one simulated ESP32
NetObserve node over the HTTP ingest path with real EWP/1 CBOR frames
(hello, status, telemetry, a DNS batch from a labelled TV). Then reads
commands on stdin:

    batch   one more DNS batch (YouTube + googlevideo) -> live push to the portal
    quit    SIGTERM the host (graceful shutdown) and exit

stdout: "READY <base> <admin token>" once paired, "OK <cmd>" per command.
Run with the server's Python (requirements.txt installed).
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SERVER_DIR = REPO / "services" / "fleet-host" / "server"
sys.path.insert(0, str(REPO / "devices" / "pi-agent"))
import ewp_cbor as cb  # noqa: E402
import ewp_protocol as P  # noqa: E402

HW = "esp32-98a316e5d18c"
TV_IP, TV_MAC = "192.168.0.42", "3c:22:fb:01:02:03"
FLEET_TOKEN, ADMIN = "e2e-fleet-token-0123456789", "e2e-admin-token-portal"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(base: str, path: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(base + path, data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def post(base: str, ch: str, frame: bytes) -> None:
    st = http(base, f"/api/v1/ewp/{HW}/{ch}?site=lab", frame,
              {"Content-Type": "application/octet-stream", "X-Fleet-Token": FLEET_TOKEN})
    if st != 200:
        raise RuntimeError(f"ingest {ch} -> HTTP {st}")


def hello() -> bytes:
    return cb.encode_frame(P.MSG_HELLO, cb.keyed("HelloBody", {
        "proto": [1, 1], "hwid": HW, "uuid": b"\x07" * 16,
        "fw": cb.keyed("FirmwareInfo", {"version": "3.1.0", "elfSha": b"\x00" * 8, "idf": "v5.5.5"}),
        "chip": cb.keyed("ChipInfo", {"model": P.CHIP_ESP32_S3, "rev": 2, "cores": 2, "flashMb": 4, "psramMb": 8}),
        "limits": cb.keyed("LinkLimits", {"maxDownlink": 4096, "maxUplink": 2048, "maxInflight": 4}),
        "tools": [[P.TOOL_READ_TELEMETRY, 1, 9], [P.TOOL_SET_WATCHLIST, 1, 8], [P.TOOL_REBOOT, 1, 8]],
        "models": [], "actuators": [], "metrics": [], "tasks": [], "mode": 0, "site": "lab", "group": "bench",
        "label": "lounge-node", "resetReason": 1, "bootCount": 1,
        "net": cb.keyed("NetObserveInfo", {"mode": 1, "port": 53, "upstreams": [socket.inet_aton("1.1.1.1")],
                                           "caps": 1, "watchVer": 1, "watchRules": 13})}))


def batch(seq: int, queries: list) -> bytes:
    return cb.encode_frame(P.MSG_EVENT, cb.keyed("EventBody", {"kind": P.EVENT_DNS_BATCH, "dns": cb.keyed("DnsBatch", {
        "seq": seq, "windowMs": 10_000,
        "clients": [[socket.inet_aton(TV_IP), bytes.fromhex(TV_MAC.replace(":", "")), "kitchen-tv"],
                    [socket.inet_aton("192.168.0.43"), b"", "laptop"]],
        "queries": queries})}), rid=seq)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="portal-e2e-"))
    labels = tmp / "device_labels.json"
    labels.write_text(json.dumps({TV_MAC: {"label": "Kitchen TV"}}))
    port = int(os.environ.get("E2E_HOST_PORT") or free_port())
    env = {**os.environ, "IONITY_MQTT_ENABLED": "false", "IONITY_DNS_ENABLED": "false",
           "IONITY_MDNS_ENABLED": "false", "IONITY_LAN_SWEEP_ENABLED": "false",
           "IONITY_SQLITE_PATH": str(tmp / "fleet.db"), "IONITY_LABELS_FILE": str(labels),
           "IONITY_REQUIRE_TOKEN": "true", "IONITY_FLEET_TOKEN": FLEET_TOKEN, "IONITY_ADMIN_TOKEN": ADMIN,
           "IONITY_DD_ENABLED": "false", "IONITY_WS_BROADCAST_INTERVAL_S": "1", "PYTHONUNBUFFERED": "1"}
    log = open(tmp / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=SERVER_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            try:
                if urllib.request.urlopen(base + "/api/v1/health", timeout=1).status == 200:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("fleet host did not start:\n" + (tmp / "server.log").read_text()[-3000:])
        post(base, "hello", hello())
        post(base, "st", cb.encode_frame(P.MSG_STATUS, cb.keyed("StatusBody", {
            "state": P.STATE_ONLINE, "ip": socket.inet_aton("192.168.0.50")})))
        post(base, "tm", cb.encode_frame(P.MSG_TELEMETRY, cb.keyed("TelemetryBody", {
            "seq": 1, "uptimeS": 3725, "mode": 0,
            "heap": cb.keyed("HeapStats", {"internalFree": 181000, "internalMinFree": 160000, "internalLargest": 90000}),
            "net": cb.keyed("NetStats", {"rssiDbm": -58, "txFail": 0, "reconnects": 0, "uplinkQueued": 0})})))
        post(base, "ev", batch(1, [[1800, 0, "www.youtube.com", 1, 0, 14, 2, P.DNSQ_FLAGGED, 1],
                                   [1000, 1, "docs.python.org", 28, 0, 30, 1, 0, 0]]))
        time.sleep(1.3)                                   # one writer cycle
        print(f"READY {base} {ADMIN}", flush=True)
        seq = 1
        for line in sys.stdin:
            cmd = line.strip()
            if cmd == "batch":
                seq += 1
                post(base, "ev", batch(seq, [[300, 0, "rr1---sn-abc.googlevideo.com", 1, 0, 22, 40, P.DNSQ_FLAGGED, 1],
                                             [200, 0, "www.youtube.com", 1, 0, 11, 1, P.DNSQ_FLAGGED, 1],
                                             [100, 1, "pypi.org", 1, 0, 25, 1, 0, 0]]))
                print("OK batch", flush=True)
            elif cmd == "quit":
                break
        return 0
    except Exception as e:                                # noqa: BLE001
        print(f"ERROR {e}", flush=True)
        return 1
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        text = (tmp / "server.log").read_text()
        if "Traceback" in text:
            sys.stderr.write(text[-4000:])


if __name__ == "__main__":
    sys.exit(main())
