#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Provision an ESP32-MCP node over USB (CLI twin of the web flasher)
Doc ID: DOC-2026-09-ESP32MCP-PROV | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

Speaks ionity-prov/1 (see firmware-arduino/Esp32_MCP_Node/Provision.ino) to a
board already running fw >= 2.0. For browsers without Web Serial, CI rigs and
bulk provisioning.

    python scripts/provision.py COM8 --get
    python scripts/provision.py COM8 --ssid IONITY-LAB-IOT --password ******** \\
        --server 192.168.0.2 --site lab --group bench --label "Bench S3 #1" --mcp-token auto --test
    python scripts/provision.py /dev/ttyUSB0 --scan
    python scripts/provision.py COM8 --factory-reset
"""
from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
import time

import serial   # pyserial

PREFIX = "IONITY-PROV "
SECRET_KEYS = {"pass", "mqtt_pass", "fleet_token", "mcp_token", "ota_pass"}


def request(port: serial.Serial, op: str, timeout: float = 5.0, **fields) -> dict:
    port.reset_input_buffer()
    port.write((json.dumps({"ionity": "prov", "op": op, **fields}) + "\n").encode())
    shown = {k: ("***" if k in SECRET_KEYS else v) for k, v in fields.items()}
    print(f">> {op} {json.dumps(shown) if shown else ''}")
    end = time.time() + timeout
    while time.time() < end:
        line = port.readline().decode("utf-8", "replace").strip()
        if not line:
            continue
        i = line.find(PREFIX)
        if i < 0:
            print(f"   {line}")
            continue
        try:
            msg = json.loads(line[i + len(PREFIX):])
        except ValueError:
            continue
        if msg.get("op") == op or (op == "get" and msg.get("op") == "hello"):
            return msg
    raise TimeoutError(f"no '{op}' reply in {timeout:.0f}s - is the board on fw >= 2.0?")


def main() -> None:
    a = argparse.ArgumentParser(description="Provision an Ionity ESP32-MCP node over USB serial")
    a.add_argument("port")
    a.add_argument("--baud", type=int, default=115200)
    a.add_argument("--get", action="store_true", help="print the board's settings")
    a.add_argument("--scan", action="store_true", help="list WiFi networks the board can see")
    a.add_argument("--ssid"); a.add_argument("--password", help="'-' to prompt")
    a.add_argument("--server", help="MCP host IP/name ('' = mDNS ionity-fleet.local)")
    a.add_argument("--mqtt-port", type=int); a.add_argument("--http-port", type=int)
    a.add_argument("--mqtt-user"); a.add_argument("--mqtt-pass")
    a.add_argument("--fleet-token"); a.add_argument("--mcp-token", help="'auto' generates one")
    a.add_argument("--ota-pass"); a.add_argument("--role", choices=["node", "standalone"])
    a.add_argument("--site"); a.add_argument("--group"); a.add_argument("--label")
    a.add_argument("--test", action="store_true", help="join WiFi + find the host after setting")
    a.add_argument("--reboot", action="store_true")
    a.add_argument("--factory-reset", action="store_true")
    o = a.parse_args()

    # dsrdtr/rtscts off and DTR/RTS low: do not hold a CH340 board in reset.
    with serial.Serial(o.port, o.baud, timeout=0.5, dsrdtr=False, rtscts=False) as p:
        p.dtr = False; p.rts = False
        time.sleep(0.3)
        hello = request(p, "hello", 6)
        print(f"== {hello.get('device_id')} fw {hello.get('fw')} chip {hello.get('chip')} "
              f"wifi={hello.get('wifi')} provisioned={hello.get('provisioned')}")

        if o.factory_reset:
            print(json.dumps(request(p, "factory_reset"), indent=2)); return
        if o.scan:
            for n in request(p, "scan", 15).get("networks", []):
                print(f"  {n['rssi']:>4} dBm  ch{n['ch']:<2} {'open ' if n['open'] else '     '}{n['ssid']}")
        fields = {k: v for k, v in {
            "ssid": o.ssid, "server": o.server, "mqtt_port": o.mqtt_port, "http_port": o.http_port,
            "mqtt_user": o.mqtt_user, "mqtt_pass": o.mqtt_pass, "fleet_token": o.fleet_token,
            "ota_pass": o.ota_pass, "role": o.role, "site": o.site, "group": o.group, "label": o.label,
        }.items() if v is not None}
        if o.password is not None:
            fields["pass"] = getpass.getpass("WiFi password: ") if o.password == "-" else o.password
        if o.mcp_token:
            fields["mcp_token"] = secrets.token_hex(18) if o.mcp_token == "auto" else o.mcp_token
            if o.mcp_token == "auto":
                print(f"   mcp_token = {fields['mcp_token']}   (keep it: write tools on http://<board>/mcp need it)")
        if fields:
            r = request(p, "set", **fields)
            if not r.get("ok"):
                sys.exit(f"refused: {r.get('error')}")
            print(f"== stored: {', '.join(r.get('changed', []))}")
        if o.test:
            r = request(p, "test", 28, timeout_ms=20000)
            print(("== WiFi OK " + f"{r.get('ip')} ({r.get('rssi')} dBm), host {r.get('host_resolved')} via "
                   f"{r.get('host_via')}, MQTT {'up' if r.get('mqtt') else 'not yet'}, MCP {r.get('mcp_url')}")
                  if r.get("ok") else f"!! {r.get('error')}")
        if o.get:
            print(json.dumps(request(p, "get"), indent=2))
        if o.reboot or fields:
            request(p, "reboot", 3)
            print("== rebooted")


if __name__ == "__main__":
    main()
