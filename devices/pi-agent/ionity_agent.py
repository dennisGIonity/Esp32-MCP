#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Ionity fleet agent for Raspberry Pi Zero / Zero 2 W / any Linux SBC
Doc ID: DOC-2026-09-ESP32MCP-PIAGENT | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

Speaks exactly the protocol the ESP32 and Pico nodes speak, so the fleet
server, dashboard and MCP tools treat a Pi like any other node:

  publish  <root>/<site>/<device_id>/telemetry      every interval_s
  publish  <root>/<site>/<device_id>/status         retained, + Last Will "offline"
  publish  <root>/<site>/<device_id>/cmd/result     replies to commands
  subscribe <root>/<site>/<device_id>/cmd  and  <root>/broadcast/cmd

Commands: ping, identify (blinks the ACT LED), reboot, set_meta (re-tag without
restarting), set_display (reported unsupported), dns_probe (raw UDP A-queries
to one resolver - same answers/format as the ESP32 firmware).

Falls back to HTTP POST /api/v1/telemetry when paho-mqtt is missing or the
broker is unreachable (telemetry keeps flowing; commands need MQTT).

Python 3.9+ standard library; paho-mqtt (apt: python3-paho-mqtt) for MQTT.
    python3 ionity_agent.py --once      # print one reading and exit
    python3 ionity_agent.py             # run (systemd: ionity-agent.service)
"""
from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import random
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

try:
    import paho.mqtt.client as paho            # type: ignore
except Exception:                              # noqa: BLE001
    paho = None

FW_VERSION = "1.0.1"   # 1.0.1: commands run off paho's thread; Will re-armed after set_meta
PRODUCT_FALLBACK = "linux-sbc"
DEFAULT_CONF = "/etc/ionity-agent.conf"
DNS_PROBE_MAX_NAMES = 12
DNS_PROBE_TIMEOUT_S = 1.5
MQTT_FAIL_THRESHOLD = 3

log = logging.getLogger("ionity-agent")


# ---------------------------------------------------------------------------
# Config + persistent state
# ---------------------------------------------------------------------------
DEFAULTS = {
    "site": "lab", "group": "bench", "label": "",
    "server": "",                       # empty = resolve mdns_host, then fallback
    "mdns_host": "ionity-fleet.local",
    "server_fallback": "192.168.124.4",
    "mqtt_port": "1883", "http_port": "8099", "mqtt_root": "ionity",
    "mqtt_username": "", "mqtt_password": "",
    "fleet_token": "dev-fleet-token-change-me",
    "interval_s": "10",
    "gf_dns": "192.168.124.3",          # default resolver for dns_probe (Gate^Flame)
    "state_file": "/var/lib/ionity-agent/state.json",
    "led": "",                          # empty = auto (ACT / led0)
}


def load_config(path: str | None) -> dict[str, str]:
    cp = configparser.ConfigParser()
    cp["agent"] = dict(DEFAULTS)
    if path and Path(path).exists():
        cp.read(path, encoding="utf-8")
    cfg = dict(cp["agent"])
    for k in DEFAULTS:                   # IONITY_AGENT_<KEY> env overrides
        v = os.environ.get(f"IONITY_AGENT_{k.upper()}")
        if v is not None:
            cfg[k] = v
    return cfg


def load_state(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        log.warning("could not save state to %s: %s", path, e)


# ---------------------------------------------------------------------------
# Identity - from the silicon, like the ESP32 eFuse MAC / RP2350 chip id
# ---------------------------------------------------------------------------
FS = Path(os.environ.get("IONITY_AGENT_FSROOT", "/"))   # tests point this at a fake /


def _read(rel: str) -> str:
    try:
        return (FS / rel.lstrip("/")).read_text(encoding="utf-8", errors="replace").strip("\x00\n ")
    except OSError:
        return ""


def device_id() -> str:
    serial = ""
    for line in _read("/proc/cpuinfo").splitlines():
        if line.lower().startswith("serial"):
            serial = line.split(":", 1)[1].strip()
    serial = serial or _read("/sys/firmware/devicetree/base/serial-number")
    serial = serial.lstrip("0") or ""
    if serial:
        return f"pi-{serial.lower()}"
    mac = _read("/sys/class/net/wlan0/address") or _read("/sys/class/net/eth0/address")
    if mac:
        return "pi-" + mac.replace(":", "").lower()
    return "pi-" + socket.gethostname().lower()


def product() -> str:
    model = _read("/proc/device-tree/model") or _read("/sys/firmware/devicetree/base/model")
    return model or PRODUCT_FALLBACK


# ---------------------------------------------------------------------------
# Metrics - names chosen to line up with the ESP32 node where they overlap
# ---------------------------------------------------------------------------
_cpu_prev: tuple[int, int] | None = None


def cpu_pct() -> float | None:
    global _cpu_prev
    line = _read("/proc/stat").splitlines()[:1]
    if not line or not line[0].startswith("cpu "):
        return None
    vals = [int(v) for v in line[0].split()[1:]]
    idle, total = vals[3] + (vals[4] if len(vals) > 4 else 0), sum(vals)
    prev, _cpu_prev = _cpu_prev, (idle, total)
    if not prev or total == prev[1]:
        return None
    return round(100.0 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


def meminfo() -> dict[str, int]:
    out = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        parts = v.split()
        if parts and parts[0].isdigit():
            out[k] = int(parts[0]) * 1024
    return out


def wifi_rssi(iface: str = "wlan0") -> float | None:
    for line in _read("/proc/net/wireless").splitlines()[2:]:
        if line.strip().startswith(iface + ":"):
            try:
                return float(line.split()[3].rstrip("."))
            except (IndexError, ValueError):
                return None
    return None


def throttled() -> int | None:
    """vcgencmd get_throttled -> bit field (0 = healthy power/thermal)."""
    exe = shutil.which("vcgencmd")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        return int(out.strip().split("=")[1], 16)
    except Exception:                              # noqa: BLE001
        return None


def collect_metrics() -> dict:
    m: dict = {}
    t = _read("/sys/class/thermal/thermal_zone0/temp")
    if t.lstrip("-").isdigit():
        m["temp_c"] = round(int(t) / 1000.0, 1)
    rssi = wifi_rssi()
    if rssi is not None:
        m["rssi_dbm"] = rssi
    mem = meminfo()
    if "MemAvailable" in mem:
        m["free_heap_bytes"] = mem["MemAvailable"]          # same name as the ESP32 metric
        if mem.get("MemTotal"):
            m["mem_used_pct"] = round(100.0 * (1 - mem["MemAvailable"] / mem["MemTotal"]), 1)
    c = cpu_pct()
    if c is not None:
        m["cpu_pct"] = c
    la = _read("/proc/loadavg").split()
    if la:
        m["load_1m"] = float(la[0])
    try:
        du = shutil.disk_usage(str(FS))
        m["disk_free_pct"] = round(100.0 * du.free / du.total, 1)
    except OSError:
        pass
    th = throttled()
    if th is not None:
        m["throttled"] = th
    return m


def uptime_s() -> int:
    up = _read("/proc/uptime").split()
    return int(float(up[0])) if up else int(time.monotonic())


def local_ip(towards: str) -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((towards, 9))              # no packet is sent for UDP connect
            return s.getsockname()[0]
    except OSError:
        return None


# ---------------------------------------------------------------------------
# DNS probe - one raw A query to ONE resolver (no system resolver, no cache),
# same answer vocabulary as the ESP32 firmware.
# ---------------------------------------------------------------------------
def build_dns_query(name: str, qid: int) -> bytes:
    labels = name.strip(".").split(".")
    if not name or any(not 0 < len(l) <= 63 for l in labels):
        raise ValueError("BADNAME")
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    for l in labels:
        q += bytes([len(l)]) + l.encode("ascii", "strict")
    return q + b"\x00" + struct.pack(">HH", 1, 1)


def parse_dns_answer(r: bytes, qid: int) -> str:
    if len(r) < 12 or struct.unpack(">H", r[:2])[0] != qid:
        return "TIMEOUT"
    rcode = r[3] & 0x0F
    if rcode:
        return {3: "NXDOMAIN", 2: "SERVFAIL", 5: "REFUSED"}.get(rcode, f"RCODE{rcode}")
    qd, an = struct.unpack(">HH", r[4:8])
    p = 12

    def skip_name(p: int) -> int:
        while p < len(r):
            b = r[p]
            if b == 0:
                return p + 1
            if b & 0xC0 == 0xC0:
                return p + 2
            p += b + 1
        return p

    for _ in range(qd):
        p = skip_name(p) + 4
    for _ in range(an):
        p = skip_name(p)
        if p + 10 > len(r):
            break
        typ, _cls, _ttl, rdlen = struct.unpack(">HHIH", r[p:p + 10])
        p += 10
        if typ == 1 and rdlen == 4 and p + 4 <= len(r):
            return ".".join(str(b) for b in r[p:p + 4])
        p += rdlen                               # CNAME etc. - keep walking
    return "NOANSWER"


def dns_probe(server: str, name: str, timeout: float = DNS_PROBE_TIMEOUT_S,
              port: int = 53) -> tuple[str, int]:
    qid = random.randint(0, 0xFFFF)
    try:
        q = build_dns_query(name, qid)
    except (ValueError, UnicodeEncodeError):
        return "BADNAME", 0
    t0 = time.monotonic()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(q, (server, port))
            while True:
                data, _ = s.recvfrom(512)
                ans = parse_dns_answer(data, qid)
                if ans != "TIMEOUT":             # ignore stray/foreign replies
                    return ans, int((time.monotonic() - t0) * 1000)
        except (socket.timeout, OSError):
            return "TIMEOUT", int((time.monotonic() - t0) * 1000)


# ---------------------------------------------------------------------------
# LED (identify)
# ---------------------------------------------------------------------------
def find_led(preferred: str = "") -> Path | None:
    base = FS / "sys/class/leds"
    for name in ([preferred] if preferred else []) + ["ACT", "led0", "act"]:
        if name and (base / name / "brightness").exists():
            return base / name
    return None


def blink_led(led: Path | None, times: int = 10, period: float = 0.12) -> bool:
    if not led:
        return False
    trig = led / "trigger"
    try:
        old = trig.read_text().split("[")[1].split("]")[0] if trig.exists() else None
        if old:
            trig.write_text("none")
        for i in range(times):
            (led / "brightness").write_text("1" if i % 2 == 0 else "0")
            time.sleep(period)
        if old:
            trig.write_text(old)                 # hand the LED back (mmc0 / heartbeat)
        return True
    except (OSError, IndexError):
        return False


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------
class Agent:
    def __init__(self, cfg: dict[str, str]):
        self.cfg = cfg
        self.state = load_state(cfg["state_file"])
        self.device_id = device_id()
        self.product = product()
        self.site = self.state.get("site") or cfg["site"]
        self.group = self.state.get("group") or cfg["group"]
        self.label = self.state.get("label") or cfg["label"] or None
        self.root = cfg["mqtt_root"]
        self.interval = max(2, int(float(cfg["interval_s"])))
        self.server, self.server_via = "", ""
        self.mqtt = None
        self.mqtt_ok = False
        self.mqtt_fails = 0
        self.use_http = paho is None
        self.tx_ok = self.tx_fail = 0
        self.stop = threading.Event()
        self.led = find_led(cfg.get("led", ""))

    # -- topics / payloads (one source of truth for MQTT and HTTP) --------
    def topic(self, leaf: str) -> str:
        return f"{self.root}/{self.site}/{self.device_id}/{leaf}"

    def telemetry(self) -> dict:
        ip = local_ip(self.server or "1.1.1.1")
        return {
            "device_id": self.device_id, "site": self.site, "group": self.group,
            "label": self.label, "fw": FW_VERSION, "product": self.product,
            "uptime_s": uptime_s(), "seq": self.tx_ok + self.tx_fail,
            "metrics": collect_metrics(),
            "net": {"ip": ip, "transport": "http" if self.use_http else "mqtt",
                    "server": self.server, "resolved_by": self.server_via},
        }

    def status(self, state: str) -> dict:
        return {"device_id": self.device_id, "site": self.site, "group": self.group,
                "label": self.label, "fw": FW_VERSION, "state": state,
                "ip": local_ip(self.server or "1.1.1.1"), "uptime_s": uptime_s(),
                "tx_ok": self.tx_ok, "tx_fail": self.tx_fail,
                "transport": "http" if self.use_http else "mqtt"}

    # -- server discovery: config -> mDNS -> fallback (as the ESP32 does) -
    def resolve_server(self) -> None:
        if self.cfg["server"]:
            self.server, self.server_via = self.cfg["server"], "config"
            return
        try:
            infos = socket.getaddrinfo(self.cfg["mdns_host"], None, socket.AF_INET)
            self.server, self.server_via = infos[0][4][0], "mdns"
        except OSError:
            self.server, self.server_via = self.cfg["server_fallback"], "fallback"
        log.info("server = %s (via %s)", self.server, self.server_via)

    # -- commands ---------------------------------------------------------
    def handle_command(self, doc: dict):
        """Returns (ok, detail, after) - `after` runs once the reply is sent."""
        action = str(doc.get("action", ""))
        if action == "ping":
            return True, "pong", None
        if action == "identify":
            ok = blink_led(self.led)
            return True, "blinked ACT LED" if ok else "no controllable LED (needs root)", None
        if action == "reboot":
            return True, "rebooting", lambda: subprocess.run(["systemctl", "reboot"], check=False)
        if action == "set_meta":
            changed = {k: str(doc[k]) for k in ("site", "group", "label") if doc.get(k)}
            if not changed:
                return False, "set_meta needs site, group or label", None
            self.state.update(changed)
            save_state(self.cfg["state_file"], self.state)
            return True, f"metadata stored: {changed}; re-topicking", lambda: self.apply_meta(changed)
        if action == "set_display":
            return False, "this node has no display", None
        if action == "dns_probe":
            return self.dns_probe_cmd(doc)
        return False, "unknown action", None

    def dns_probe_cmd(self, doc: dict):
        server = str(doc.get("dns_server") or self.state.get("gf_dns") or self.cfg["gf_dns"])
        try:
            socket.inet_aton(server)
        except OSError:
            return False, "dns_server is not an IPv4 address", None
        if doc.get("remember"):
            self.state["gf_dns"] = server
            save_state(self.cfg["state_file"], self.state)
        answers, blocked, resolved = [], 0, 0
        for name in list(doc.get("names") or [])[:DNS_PROBE_MAX_NAMES]:
            ans, ms = dns_probe(server, str(name))
            answers.append({"n": name, "a": ans, "ms": ms})
            blocked += ans in ("0.0.0.0", "::")
            resolved += ans[:1].isdigit() and ans != "0.0.0.0"
            log.info("dns_probe %s @%s -> %s (%dms)", name, server, ans, ms)
        res = {"server": server, "from": local_ip(server), "answers": answers,
               "blocked": blocked, "resolved": resolved, "asked": len(answers)}
        return True, json.dumps(res), None

    def apply_meta(self, changed: dict) -> None:
        old_cmd = self.topic("cmd")
        if self.mqtt and self.mqtt_ok:
            self.mqtt.publish(self.topic("status"), json.dumps(self.status("offline")), qos=1, retain=True)
        self.site = changed.get("site", self.site)
        self.group = changed.get("group", self.group)
        self.label = changed.get("label", self.label)
        if self.mqtt and self.mqtt_ok:
            self.mqtt.unsubscribe(old_cmd)
            self.mqtt.subscribe(self.topic("cmd"), 1)
            self.mqtt.publish(self.topic("status"), json.dumps(self.status("online")), qos=1, retain=True)
            # A Will is only registered at CONNECT, so will_set() on a live
            # session changes nothing until the next connect. Re-arm it on the
            # new topic by bouncing the session; paho's loop thread reconnects.
            self.mqtt.will_set(self.topic("status"), json.dumps(self.status("offline")), qos=1, retain=True)
            try:
                self.mqtt.disconnect()
                self.mqtt.reconnect()
            except Exception:                      # noqa: BLE001 - loop_start() keeps retrying
                log.debug("reconnect after set_meta deferred to paho", exc_info=True)

    # -- MQTT -------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        ok = (rc == 0) if isinstance(rc, int) else not rc.is_failure
        if not ok:
            log.warning("MQTT connect refused: %s", rc)
            return
        self.mqtt_ok, self.mqtt_fails, self.use_http = True, 0, False
        client.subscribe([(self.topic("cmd"), 1), (f"{self.root}/broadcast/cmd", 1)])
        client.publish(self.topic("status"), json.dumps(self.status("online")), qos=1, retain=True)
        log.info("MQTT connected -> %s:%s", self.server, self.cfg["mqtt_port"])

    def _on_disconnect(self, client, userdata, *args):
        if self.mqtt_ok:
            log.warning("MQTT disconnected; paho reconnects, HTTP carries telemetry meanwhile")
        self.mqtt_ok = False

    def _on_message(self, client, userdata, msg):
        try:
            doc = json.loads(msg.payload.decode("utf-8", "replace") or "{}")
        except ValueError:
            return
        if not isinstance(doc, dict):
            return
        log.info("cmd <- %s", doc.get("action"))
        # Never block paho's network thread: a dns_probe can take 18 s, during
        # which no PINGREQ/PUBLISH would be processed.
        threading.Thread(target=self._run_command, args=(client, doc), daemon=True,
                         name=f"cmd-{doc.get('cmd_id', '')}").start()

    def _run_command(self, client, doc: dict) -> None:
        try:
            ok, detail, after = self.handle_command(doc)
        except Exception as e:                     # noqa: BLE001 - never kill the worker
            ok, detail, after = False, f"agent error: {e}", None
        reply = {"device_id": self.device_id, "cmd_id": doc.get("cmd_id", ""), "ok": ok, "detail": detail}
        info = client.publish(self.topic("cmd/result"), json.dumps(reply), qos=1)
        if after:
            try:
                info.wait_for_publish(2)
            except Exception:                      # noqa: BLE001
                log.debug("result publish not confirmed before after-action", exc_info=True)
            after()

    def start_mqtt(self) -> None:
        if paho is None:
            log.warning("paho-mqtt not installed (apt install python3-paho-mqtt) - HTTP only, no commands")
            return
        try:
            c = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2,
                            client_id=self.device_id, clean_session=True)
        except AttributeError:                     # paho < 2.0 (Bullseye apt package)
            c = paho.Client(client_id=self.device_id, clean_session=True)
        if self.cfg["mqtt_username"]:
            c.username_pw_set(self.cfg["mqtt_username"], self.cfg["mqtt_password"])
        c.will_set(self.topic("status"), json.dumps(self.status("offline")), qos=1, retain=True)
        c.on_connect, c.on_disconnect, c.on_message = self._on_connect, self._on_disconnect, self._on_message
        c.reconnect_delay_set(min_delay=2, max_delay=60)
        c.connect_async(self.server, int(self.cfg["mqtt_port"]), keepalive=60)
        c.loop_start()
        self.mqtt = c

    # -- transmit ---------------------------------------------------------
    def send_http(self, body: dict) -> bool:
        url = f"http://{self.server}:{self.cfg['http_port']}/api/v1/telemetry"
        req = urllib.request.Request(url, json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Fleet-Token": self.cfg["fleet_token"]})
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                return 200 <= r.status < 300
        except Exception as e:                     # noqa: BLE001
            log.warning("HTTP ingest failed: %s", e)
            return False

    def transmit(self) -> bool:
        if self.mqtt and self.mqtt_ok:
            body = self.telemetry()
            ok = self.mqtt.publish(self.topic("telemetry"), json.dumps(body), qos=0).rc == 0
        else:
            if self.mqtt:
                self.mqtt_fails += 1
                if self.mqtt_fails >= MQTT_FAIL_THRESHOLD and not self.use_http:
                    log.warning("no broker after %d tries - falling back to HTTP ingest", self.mqtt_fails)
                    self.use_http = True
            if not self.use_http:
                return False
            ok = self.send_http(self.telemetry())
            if not ok and self.mqtt_fails % 5 == 0:
                self.resolve_server()              # the server may have moved
        self.tx_ok += ok
        self.tx_fail += not ok
        return ok

    # -- lifecycle --------------------------------------------------------
    def run(self) -> None:
        log.info("Ionity agent %s | %s | %s | site/group %s/%s", FW_VERSION, self.device_id,
                 self.product, self.site, self.group)
        self.resolve_server()
        self.start_mqtt()
        time.sleep(1.5)                            # give MQTT a moment before the first reading
        while not self.stop.is_set():
            self.transmit()
            self.stop.wait(self.interval)
        self.shutdown()

    def shutdown(self) -> None:
        if self.mqtt:
            try:
                if self.mqtt_ok:
                    self.mqtt.publish(self.topic("status"), json.dumps(self.status("offline")),
                                      qos=1, retain=True).wait_for_publish(2)
                self.mqtt.disconnect()
            finally:
                self.mqtt.loop_stop()
        log.info("stopped")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ionity fleet agent (Raspberry Pi / Linux)")
    ap.add_argument("--config", default=os.environ.get("IONITY_AGENT_CONFIG", DEFAULT_CONF))
    ap.add_argument("--once", action="store_true", help="print one telemetry reading as JSON and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    agent = Agent(load_config(a.config))
    if a.once:
        agent.resolve_server()
        cpu_pct()                                  # prime the CPU delta
        time.sleep(0.3)
        print(json.dumps(agent.telemetry(), indent=2))
        return 0
    signal.signal(signal.SIGTERM, lambda *_: agent.stop.set())
    signal.signal(signal.SIGINT, lambda *_: agent.stop.set())
    agent.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
