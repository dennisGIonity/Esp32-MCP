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


# --------------------------------------------------------------------------
# Inbound commands (1.1.0): MQTT cmd topic -> "CMD {json}" on the board's serial,
# "RES {json}" from the board -> MQTT cmd/result. A board with no radio can
# then be commanded exactly like a networked one.
# --------------------------------------------------------------------------
PORT_OF: dict[str, "serial.Serial"] = {}      # device_id -> open serial handle
SITE_OF: dict[str, str] = {}
WRITE_LOCK = threading.Lock()
MQTT = None
MQTT_ROOT = "ionity"


def dns_query(server: str, name: str, timeout: float = 1.5) -> tuple[str, int]:
    """One raw A query to ONE resolver - same semantics as the ESP32's dnsProbe."""
    import random
    import socket
    import struct
    tid = random.randint(0, 0xFFFF)
    q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    for part in name.strip(".").split("."):
        if not part or len(part) > 63:
            return "BADNAME", 0
        q += bytes([len(part)]) + part.encode("idna")
    q += b"\x00" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    t0 = time.monotonic()
    try:
        s.sendto(q, (server, 53))
        while True:
            d, _ = s.recvfrom(512)
            if len(d) >= 12 and d[:2] == q[:2]:
                break
    except OSError:
        return "TIMEOUT", int((time.monotonic() - t0) * 1000)
    finally:
        s.close()
    ms = int((time.monotonic() - t0) * 1000)
    rcode = d[3] & 0x0F
    if rcode:
        return {3: "NXDOMAIN", 2: "SERVFAIL", 5: "REFUSED"}.get(rcode, f"RCODE{rcode}"), ms
    an = struct.unpack(">H", d[6:8])[0]
    p = 12
    while d[p] != 0:
        if d[p] & 0xC0 == 0xC0:
            p += 1
            break
        p += d[p] + 1
    p += 5
    for _ in range(an):
        if d[p] & 0xC0 == 0xC0:
            p += 2
        else:
            while d[p] != 0:
                p += d[p] + 1
            p += 1
        rtype, _, _, rdlen = struct.unpack(">HHIH", d[p:p + 10])
        p += 10
        if rtype == 1 and rdlen == 4:
            return ".".join(str(b) for b in d[p:p + 4]), ms
        p += rdlen
    return "NOANSWER", ms


def lab_source_ip(server: str) -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((server, 53))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def serial_write(device_id: str, line: str) -> bool:
    s = PORT_OF.get(device_id)
    if not s:
        return False
    with WRITE_LOCK:
        s.write((line + "\n").encode())
        s.flush()
    return True


def publish_result(result: dict) -> None:
    did = result.get("device_id", "")
    topic = f"{MQTT_ROOT}/{SITE_OF.get(did, 'lab')}/{did}/cmd/result"
    if MQTT:
        MQTT.publish(topic, json.dumps(result), qos=1)
    log(f"result {did} {result.get('cmd_id')}: ok={result.get('ok')}")


def start_mqtt(host: str, port: int) -> None:
    global MQTT
    try:
        import paho.mqtt.client as paho
    except ImportError:
        log("paho-mqtt not installed - serial boards will report but cannot be commanded")
        return
    try:
        c = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2,
                        client_id="ionity-serial-bridge", clean_session=True)
    except AttributeError:
        c = paho.Client(client_id="ionity-serial-bridge", clean_session=True)

    def on_connect(client, *_a, **_k):
        client.subscribe(f"{MQTT_ROOT}/+/+/cmd", qos=1)
        client.subscribe(f"{MQTT_ROOT}/broadcast/cmd", qos=1)
        log(f"MQTT connected {host}:{port} - relaying commands to serial boards")

    def on_message(_client, _ud, msg):
        parts = msg.topic.split("/")
        try:
            body = json.loads(msg.payload.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return
        targets = list(PORT_OF) if parts[1] == "broadcast" else [parts[2]]
        for did in targets:
            if did in PORT_OF and serial_write(did, "CMD " + json.dumps(body)):
                log(f"cmd -> {did} over serial: {body.get('action')}")

    c.on_connect = on_connect
    c.on_message = on_message
    c.reconnect_delay_set(min_delay=1, max_delay=30)
    c.connect_async(host, port, keepalive=60)
    c.loop_start()
    MQTT = c


def handle_dnsq(device_id: str, line: str) -> None:
    try:
        q = json.loads(line[5:])
    except json.JSONDecodeError:
        return
    server = q.get("server") or "192.168.124.3"
    answers = []
    for name in (q.get("names") or [])[:12]:
        a, ms = dns_query(server, str(name))
        answers.append({"n": name, "a": a, "ms": ms})
        log(f"dns_probe for {device_id}: {name} @{server} -> {a} ({ms}ms)")
    serial_write(device_id, "DNSA " + json.dumps({
        "cmd_id": q.get("cmd_id"), "server": server,
        "from": lab_source_ip(server), "answers": answers}))


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
                    if line.startswith("RES "):
                        try:
                            publish_result(json.loads(line[4:]))
                        except json.JSONDecodeError:
                            pass
                        continue
                    if line.startswith("DNSQ "):
                        did = next((d for d, h in PORT_OF.items() if h is s), "")
                        threading.Thread(target=handle_dnsq, args=(did, line), daemon=True).start()
                        continue
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
                    did = payload.get("device_id", "")
                    if did:
                        PORT_OF[did] = s
                        SITE_OF[did] = payload.get("site", "lab")
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
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    a = ap.parse_args()

    started: set[str] = set()
    log(f"forwarding to {a.server}")
    start_mqtt(a.mqtt_host, a.mqtt_port)
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
