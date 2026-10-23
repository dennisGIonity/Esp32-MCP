#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Provision an ESP32-MCP node over USB (CLI twin of the portal)
Doc ID: DOC-2026-09-ESP32MCP-PROV | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

Speaks both serial provisioning dialects, like the portal (@ionity/prov):

  ionity-prov/1  fw 2.x (firmware/legacy-arduino/Esp32_MCP_Node/Provision.ino):
                 plain JSON lines, replies matched by "op".
  ionity-prov/2  fw 3.x (firmware/esp32-node, components/provisioning):
                 "v":2 + "id" in every request, a CRC-32 suffix " *xxxxxxxx" on
                 both directions, structured `test` replies, field-level errors.

"hello" always goes out in the prov/1 form (fw 2.x ignores CRC-suffixed lines);
a fw 3 board answers it with "prov":2 and every later request uses prov/2.
Fields a board does not advertise in its hello "set_fields" are never sent.

    python tools/scripts/provision.py COM8 --get
    python tools/scripts/provision.py COM8 --ssid "Afrihost Fibre DTM" --password - --role node --test
    python tools/scripts/provision.py COM8 --host-url mqtt://192.168.0.3:1883 --dns-mode forward
    python tools/scripts/provision.py /dev/ttyUSB0 --scan
    python tools/scripts/provision.py COM8 --factory-reset
"""
from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
import time
import zlib

import serial   # pyserial

PREFIX = "IONITY-PROV "
SECRET_KEYS = {"pass", "mqtt_pass", "fleet_token", "mcp_token", "ota_pass"}
DNS_DEFAULT = "1.1.1.1,8.8.8.8"


def crc_hex(text: str) -> str:
    return f"{zlib.crc32(text.encode('utf-8')) & 0xFFFFFFFF:08x}"


def parse_line(line: str) -> tuple[dict | None, str]:
    """(reply, crc state 'ok' | 'absent' | 'mismatch') for an IONITY-PROV line, (None, '') otherwise."""
    i = line.find(PREFIX)
    if i < 0:
        return None, ""
    body = line[i + len(PREFIX):].rstrip()
    crc = "absent"
    if len(body) > 10 and body[-10:-8] == " *":
        want = body[-8:]
        body = body[:-10]
        crc = "ok" if crc_hex(body) == want else "mismatch"
    try:
        msg = json.loads(body)
    except ValueError:
        return None, ""
    return (msg if isinstance(msg, dict) and isinstance(msg.get("op"), str) else None), crc


def valid_upstreams(text: str) -> str:
    """dns_upstream: '' or 1-2 IPv4 resolvers, same rules as the firmware and @ionity/prov dns.ts."""
    s = text.strip()
    if not s:
        return ""
    items = [x.strip() for x in s.split(",")]
    if len(items) > 2:
        raise argparse.ArgumentTypeError("at most 2 upstream resolvers")
    seen = set()
    for it in items:
        parts = it.split(".")
        if len(parts) != 4 or not all(p.isdigit() and (p == "0" or not p.startswith("0")) and int(p) <= 255 for p in parts):
            raise argparse.ArgumentTypeError(f"'{it}' is not an IPv4 address")
        first = int(parts[0])
        if first in (0, 127) or first >= 224:
            raise argparse.ArgumentTypeError(f"{it} cannot be a DNS upstream (loopback, multicast or reserved)")
        if it in seen:
            raise argparse.ArgumentTypeError(f"{it} is listed twice")
        seen.add(it)
    return ",".join(items)


class Board:
    def __init__(self, port: serial.Serial):
        self.port = port
        self.v2 = False
        self.next_id = 1
        self.set_fields: set[str] = set()

    def request(self, op: str, timeout: float = 5.0, **fields) -> dict:
        v2 = self.v2 and op != "hello"
        env: dict = {"ionity": "prov", "op": op}
        rid = None
        if v2:
            rid = self.next_id
            self.next_id += 1
            env.update(v=2, id=rid)
        env.update(fields)
        text = json.dumps(env, separators=(",", ":"))
        line = f"{text} *{crc_hex(text)}\n" if v2 else text + "\n"
        self.port.reset_input_buffer()
        self.port.write(line.encode("utf-8"))
        shown = {k: ("***" if k in SECRET_KEYS and v else v) for k, v in fields.items()}
        print(f">> {op} {json.dumps(shown) if shown else ''}")
        end = time.time() + timeout
        while time.time() < end:
            raw = self.port.readline().decode("utf-8", "replace").strip()
            if not raw:
                continue
            msg, crc = parse_line(raw)
            if msg is None:
                print(f"   {raw}")
                continue
            if crc == "mismatch":
                print("   (reply failed its CRC check - ignored)")
                continue
            if rid is not None and msg.get("id") == rid:
                return msg
            if msg.get("op") == op or (op == "get" and msg.get("op") == "hello"):
                return msg
        raise TimeoutError(f"no '{op}' reply in {timeout:.0f}s - is the board on fw >= 2.0?")

    def hello(self) -> dict:
        h = self.request("hello", 6)
        self.v2 = h.get("prov") == 2
        self.set_fields = set(h.get("set_fields") or [])
        return h


def main() -> None:
    a = argparse.ArgumentParser(description="Provision an Ionity ESP32-MCP node over USB serial")
    a.add_argument("port")
    a.add_argument("--baud", type=int, default=115200)
    a.add_argument("--get", action="store_true", help="print the board's settings")
    a.add_argument("--scan", action="store_true", help="list WiFi networks the board can see")
    a.add_argument("--ssid"); a.add_argument("--password", help="'-' to prompt")
    a.add_argument("--host-url", help="fw 3: mqtt://host:port or mqtts://... ('' = mDNS ionity-fleet.local)")
    a.add_argument("--server", help="MCP host IP/name ('' = mDNS ionity-fleet.local); fw 3 gets it as host_url")
    a.add_argument("--mqtt-port", type=int); a.add_argument("--http-port", type=int)
    a.add_argument("--mqtt-user"); a.add_argument("--mqtt-pass")
    a.add_argument("--fleet-token"); a.add_argument("--mcp-token", help="'auto' generates one")
    a.add_argument("--ota-pass"); a.add_argument("--role", choices=["node", "standalone"])
    a.add_argument("--site"); a.add_argument("--group"); a.add_argument("--label")
    a.add_argument("--dns-mode", choices=["off", "forward"],
                   help="fw >= 3.1: LAN DNS forwarder (point the router's DHCP DNS at the board)")
    a.add_argument("--dns-upstream", type=valid_upstreams,
                   help=f"fw >= 3.1: 1-2 IPv4 resolvers, '' = {DNS_DEFAULT}")
    a.add_argument("--test", action="store_true", help="join WiFi + reach the host after setting")
    a.add_argument("--reboot", action="store_true")
    a.add_argument("--factory-reset", action="store_true")
    o = a.parse_args()

    # dsrdtr/rtscts off and DTR/RTS low: do not hold a CH340 board in reset.
    with serial.Serial(o.port, o.baud, timeout=0.5, dsrdtr=False, rtscts=False) as p:
        p.dtr = False; p.rts = False
        time.sleep(0.3)
        b = Board(p)
        hello = b.hello()
        print(f"== {hello.get('hwid') or hello.get('device_id')} fw {hello.get('fw')} chip {hello.get('chip')} "
              f"prov/{2 if b.v2 else 1} wifi={hello.get('wifi')} provisioned={hello.get('provisioned')}")

        if o.factory_reset:
            print(json.dumps(b.request("factory_reset"), indent=2)); return
        if hello.get("wifi_error"):
            print(f"!! last WiFi attempt: {hello['wifi_error']}")
        if o.scan:
            r = b.request("scan", 20)
            if not r.get("ok"):
                print(f"!! scan: {r.get('error', 'failed')}")
            for n in r.get("networks", []):
                print(f"  {n['rssi']:>4} dBm  ch{n['ch']:<2} {'open ' if n['open'] else '     '}{n['ssid'] or '(hidden)'}")

        fields = {k: v for k, v in {
            "ssid": o.ssid, "mqtt_user": o.mqtt_user, "mqtt_pass": o.mqtt_pass, "fleet_token": o.fleet_token,
            "ota_pass": o.ota_pass, "role": o.role, "site": o.site, "group": o.group, "label": o.label,
        }.items() if v is not None}
        if b.v2:
            # fw 3 stores the MCP host as one URL; server/mqtt_port are prov/1 names.
            if o.host_url is not None:
                fields["host_url"] = o.host_url
            elif o.server is not None:
                fields["host_url"] = f"mqtt://{o.server}:{o.mqtt_port or 1883}" if o.server else ""
            if o.dns_mode is not None:
                fields["dns_mode"] = o.dns_mode
            if o.dns_upstream is not None:
                fields["dns_upstream"] = o.dns_upstream
        else:
            for k, v in (("server", o.server), ("mqtt_port", o.mqtt_port), ("http_port", o.http_port)):
                if v is not None:
                    fields[k] = v
            if o.host_url:
                print("!! this board runs fw 2.x: use --server/--mqtt-port instead of --host-url")
        if o.password is not None:
            fields["pass"] = getpass.getpass("WiFi password: ") if o.password == "-" else o.password
        if o.mcp_token:
            fields["mcp_token"] = secrets.token_hex(18) if o.mcp_token == "auto" else o.mcp_token
            if o.mcp_token == "auto":
                print(f"   mcp_token = {fields['mcp_token']}   (keep it: write tools on http://<board>/mcp need it)")
        if b.v2 and b.set_fields:
            for k in [k for k in fields if k not in b.set_fields]:
                print(f"!! this firmware does not accept '{k}' - left out")
                del fields[k]
        elif not b.v2 and (o.dns_mode or o.dns_upstream is not None):
            print("!! fw 2.x has no LAN DNS forwarder (it arrives with fw 3.1) - DNS settings left out")

        if fields:
            r = b.request("set", **fields)
            if not r.get("ok"):
                where = f" (field {r['field']})" if r.get("field") else ""
                sys.exit(f"refused{where}: {r.get('error')}")
            print(f"== stored: {', '.join(r.get('changed', []))}")
        if o.test:
            r = b.request("test", 28, timeout_ms=20000)
            if "wifi" in r and isinstance(r["wifi"], dict):     # prov/2: structured
                w, br = r["wifi"], r.get("broker") or {}
                if w.get("ok"):
                    print(f"== WiFi OK {w.get('ip')} ({w.get('rssi')} dBm); host "
                          f"{'reached' if br.get('ok') else 'NOT reached' if br.get('ok') is False else 'n/a'}"
                          f"{' - ' + br['detail'] if br.get('detail') else ''}")
                else:
                    print(f"!! WiFi: {w.get('error')}")
            else:
                print(("== WiFi OK " + f"{r.get('ip')} ({r.get('rssi')} dBm), host {r.get('host_resolved')} via "
                       f"{r.get('host_via')}, MQTT {'up' if r.get('mqtt') else 'not yet'}, MCP {r.get('mcp_url')}")
                      if r.get("ok") else f"!! {r.get('error')}")
        if o.get:
            print(json.dumps(b.request("get"), indent=2))
        if o.reboot or fields:
            try:
                b.request("reboot", 3)
            except TimeoutError:
                pass                                       # the reply can be lost in the reset
            print("== rebooted")


if __name__ == "__main__":
    main()
