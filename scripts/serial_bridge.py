#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | USB-serial telemetry bridge
Doc ID: DOC-2026-09-ESP32MCP-SERBRIDGE | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

Lets a board with no network of its own (a plain Raspberry Pi Pico, a board
whose WiFi is down) still report into the fleet. The node prints one line per
reading:

    TLM {"device_id": "...", "metrics": {...}, ...}

This forwards each line to the fleet server exactly as if the board had
POSTed it, tagged net.transport = "serial".

    python scripts/serial_bridge.py                 # auto: every RP2040/RP2350 port
    python scripts/serial_bridge.py --port COM9
    python scripts/serial_bridge.py --port COM9 --port COM12

It releases a port whenever it errors (e.g. while the board is being
re-flashed) and reopens it automatically afterwards.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request
from pathlib import Path

import serial
from serial.tools import list_ports

RP_VIDS = {0x2E8A}          # Raspberry Pi (RP2040 / RP2350)

# Serial-only boards cannot receive set_meta (there is no inbound path to
# them), so their label/site/group are applied here instead:
#   {"pico-c18354253344139d": {"label": "lab-node-03", "site": "lab", "group": "bench"}}
LABELS_FILE = Path(__file__).resolve().parents[1] / "config" / "device_labels.json"


def load_labels() -> dict:
    try:
        return json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:                          # noqa: BLE001
        log(f"could not read {LABELS_FILE.name}: {e}")
        return {}


def log(m: str) -> None:
    print(f"[serial-bridge] {time.strftime('%H:%M:%S')} {m}", flush=True)


def post(server: str, payload: dict) -> bool:
    req = urllib.request.Request(
        f"{server}/api/v1/telemetry", json.dumps(payload).encode(),
        {"Content-Type": "application/json",
         "X-Fleet-Token": "dev-fleet-token-change-me"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return 200 <= r.status < 300
    except Exception as e:                          # noqa: BLE001
        log(f"POST failed: {e}")
        return False


def pump(port: str, baud: int, server: str) -> None:
    sent = 0
    while True:
        try:
            with serial.Serial(port, baud, timeout=2) as s:
                log(f"{port} open")
                while True:
                    raw = s.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("TLM "):
                        continue
                    try:
                        payload = json.loads(line[4:])
                    except json.JSONDecodeError:
                        continue
                    net = payload.setdefault("net", {})
                    # A reading that arrives here came over USB, whatever the
                    # board believed. Record the path it actually took.
                    net["transport"] = "serial"
                    # Re-read each time so edits apply without a restart.
                    meta = load_labels().get(payload.get("device_id", ""), {})
                    for k in ("label", "site", "group"):
                        if meta.get(k):
                            payload[k] = meta[k]
                    if post(server, payload):
                        sent += 1
                        if sent == 1 or sent % 30 == 0:
                            log(f"{port} -> {payload.get('device_id')} "
                                f"({sent} readings forwarded)")
        except serial.SerialException as e:
            log(f"{port} unavailable ({e.__class__.__name__}); retrying in 5s")
            time.sleep(5)
        except Exception as e:                      # noqa: BLE001
            log(f"{port} error: {e}; retrying in 5s")
            time.sleep(5)


def discover() -> list[str]:
    return [p.device for p in list_ports.comports() if p.vid in RP_VIDS]


def main() -> None:
    ap = argparse.ArgumentParser(description="Forward TLM lines from USB serial to the fleet")
    ap.add_argument("--port", action="append", default=[])
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--server", default="http://127.0.0.1:8099")
    a = ap.parse_args()

    started: set[str] = set()
    log(f"forwarding to {a.server}")
    while True:
        ports = a.port or discover()
        for p in ports:
            if p not in started:
                started.add(p)
                threading.Thread(target=pump, args=(p, a.baud, a.server),
                                 daemon=True).start()
        if not ports and not started:
            log("no RP2040/RP2350 serial ports found yet; watching")
        time.sleep(15)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
