#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Ionity fleet agent 2 for Raspberry Pi / any Linux SBC
Doc ID: DOC-2026-10-ESP32MCP-PIAGENT2 | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

A Raspberry Pi is a first-class fleet node: it speaks EWP/1 - the binary
contract of packages/protocol - byte for byte like the ESP-IDF firmware 3, so
the fleet host has ONE decoder, ONE device model and ONE DNS pipeline:

  ionity/ewp1/<site>/<hwid>/hello   HELLO   retained   capabilities, tools, NetObserve info
  ionity/ewp1/<site>/<hwid>/st      STATUS  retained   ONLINE; OFFLINE is the Last Will
  ionity/ewp1/<site>/<hwid>/tm      TELEMETRY  QoS 0   heap/net/cpu/sensors + dns counters
  ionity/ewp1/<site>/<hwid>/ev      EVENT      QoS 1   DNS_BATCH every flush, DNS_FLAGGED at once
  ionity/ewp1/<site>/<hwid>/dn      REQ / CANCEL from the host (and ionity/ewp1/_all/dn)
  ionity/ewp1/<site>/<hwid>/rs      ACK / RES / ERR

With dns_mode = forward the Pi is the LAN's DNS forwarder (netobserve.py): point
the router's DHCP "DNS server" at the Pi and every device's lookups are logged
per device and checked against the watchlist (YouTube built in).

Tools: get_device_info, read_telemetry, identify, set_actuator (alert LED),
set_watchlist, get_watchlist, get_network_stats, get_local_devices,
query_dns_log, scan_lan, flush_dns_cache, reboot.

No broker? Frames are POSTed unchanged to the host
(POST /api/v1/ewp/<hwid>/<channel>, X-Fleet-Token); commands need MQTT.

Python 3.9+ standard library; paho-mqtt (apt: python3-paho-mqtt) for MQTT.
    python3 ionity_agent.py --once      # print HELLO + one TELEMETRY (decoded) and exit
    python3 ionity_agent.py             # run (systemd: ionity-agent.service)
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import logging
import os
import platform
import random
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid as uuidlib
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ewp_cbor as cbor            # noqa: E402
import ewp_protocol as P           # noqa: E402
import netobserve as nob           # noqa: E402

try:
    import paho.mqtt.client as paho            # type: ignore
except Exception:                              # noqa: BLE001
    paho = None

FW_VERSION = "2.0.0"   # 2.0.0: EWP/1 (same contract as firmware 3), NetObserve DNS forwarder, watchlist
PRODUCT_FALLBACK = "linux-sbc"
DEFAULT_CONF = "/etc/ionity-agent.conf"
MQTT_FAIL_THRESHOLD = 3
MAX_INFLIGHT = P.LIMITS["maxInflight"]
UPLINK_MAX = P.LIMITS["uplinkFrame"]
DEDUPE = 32

log = logging.getLogger("ionity-agent")


# ---------------------------------------------------------------------------
# Config + persistent state
# ---------------------------------------------------------------------------
DEFAULTS = {
    "site": "lab", "group": "bench", "label": "",
    "server": "",                       # empty = resolve mdns_host, then server_fallback
    "mdns_host": "ionity-fleet.local",
    "server_fallback": "",
    "mqtt_port": "1883", "http_port": "8099",
    "mqtt_username": "", "mqtt_password": "",
    "fleet_token": "",
    "interval_s": "10",
    "state_file": "/var/lib/ionity-agent/state.json",
    "led": "",                          # identify LED; empty = auto (ACT / led0)
    "alert_led": "",                    # watchlist alarm LED; empty = same as led
    "alert_hold_s": "20",
    # NetObserve - the LAN DNS forwarder
    "dns_mode": "off",                  # off | forward
    "dns_upstream": "1.1.1.1,8.8.8.8",
    "dns_port": "53",
    "dns_bind": "",                     # empty = all addresses (falls back to the LAN address)
    "dns_flush_s": "10",
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
# Identity - from the silicon, like the ESP32 eFuse MAC
# ---------------------------------------------------------------------------
FS = Path(os.environ.get("IONITY_AGENT_FSROOT", "/"))   # tests point this at a fake /


def _read(rel: str) -> str:
    try:
        return (FS / rel.lstrip("/")).read_text(encoding="utf-8", errors="replace").strip("\x00\n ")
    except OSError:
        return ""


def _hex(s: str) -> str:
    return "".join(c for c in s.lower() if c in "0123456789abcdef")


def device_id() -> str:
    """pi-<8..16 hex>: CPU serial (leading zeros dropped), else the NIC MAC, else a
    hash of the host name. Matches @ionity/ewp hwid() so it is a valid topic level."""
    serial = ""
    for line in _read("/proc/cpuinfo").splitlines():
        if line.lower().startswith("serial"):
            serial = line.split(":", 1)[1].strip()
    serial = _hex(serial or _read("/sys/firmware/devicetree/base/serial-number")).lstrip("0")
    if serial:
        return "pi-" + serial[-16:].rjust(8, "0")
    mac = _hex(_read("/sys/class/net/wlan0/address") or _read("/sys/class/net/eth0/address"))
    if len(mac) == 12:
        return "pi-" + mac
    return "pi-" + hashlib.sha256(socket.gethostname().encode()).hexdigest()[:12]


def product() -> str:
    model = _read("/proc/device-tree/model") or _read("/sys/firmware/devicetree/base/model")
    return model or PRODUCT_FALLBACK


# ---------------------------------------------------------------------------
# Metrics - Linux sources mapped onto the TELEMETRY body
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
    """Named Linux metrics; the open-ended ones travel as TELEMETRY.ext."""
    m: dict = {}
    t = _read("/sys/class/thermal/thermal_zone0/temp")
    if t.lstrip("-").isdigit():
        m["temp_c"] = round(int(t) / 1000.0, 1)
    rssi = wifi_rssi()
    if rssi is not None:
        m["rssi_dbm"] = rssi
    mem = meminfo()
    if "MemAvailable" in mem:
        m["free_heap_bytes"] = mem["MemAvailable"]
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


# [metric id, name, unit] - HELLO.metrics; TELEMETRY.ext carries {id: value}
EXT_METRICS = ((1, "mem_used_pct", "%"), (2, "load_1m", ""), (3, "disk_free_pct", "%"), (4, "throttled", ""))


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
# LEDs (identify + watchlist alarm)
# ---------------------------------------------------------------------------
def find_led(preferred: str = "") -> Path | None:
    base = FS / "sys/class/leds"
    for name in ([preferred] if preferred else []) + ["ACT", "led0", "act"]:
        if name and (base / name / "brightness").exists():
            return base / name
    return None


class Led:
    """A sysfs LED we can take from its kernel trigger and hand back."""

    def __init__(self, path: Path | None):
        self.path = path
        self.lock = threading.Lock()
        self.saved_trigger: str | None = None
        self.value = 0.0

    def _trigger(self) -> Path | None:
        return self.path / "trigger" if self.path else None

    def set(self, on: bool) -> bool:
        if not self.path:
            return False
        with self.lock:
            try:
                trig = self._trigger()
                if on and self.saved_trigger is None and trig and trig.exists():
                    cur = trig.read_text()
                    self.saved_trigger = cur.split("[")[1].split("]")[0] if "[" in cur else "none"
                    trig.write_text("none")
                (self.path / "brightness").write_text("1" if on else "0")
                if not on and self.saved_trigger is not None and trig:
                    trig.write_text(self.saved_trigger)      # hand the LED back (mmc0 / heartbeat)
                    self.saved_trigger = None
                self.value = 1.0 if on else 0.0
                return True
            except (OSError, IndexError):
                return False

    def blink(self, times: int = 10, period: float = 0.12) -> bool:
        if not self.path:
            return False
        ok = True
        for i in range(times):
            ok = self.set(i % 2 == 0) and ok
            time.sleep(period)
        self.set(False)
        return ok


# ---------------------------------------------------------------------------
# Tool errors -> ERR frames
# ---------------------------------------------------------------------------
class ToolError(Exception):
    def __init__(self, status: int, detail: str, arg_key: int | None = None, retry_after_ms: int | None = None):
        super().__init__(detail)
        self.status, self.detail, self.arg_key, self.retry_after_ms = status, detail, arg_key, retry_after_ms


def _u(v, lo: int, hi: int, key: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ToolError(P.ST_INVALID_ARGS, f"argument {key} must be an integer in [{lo}, {hi}]", key)
    return v


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------
class Agent:
    def __init__(self, cfg: dict[str, str]):
        self.cfg = cfg
        self.state = load_state(cfg["state_file"])
        self.hwid = device_id()
        self.device_id = self.hwid
        self.product = product()
        self.site = self.state.get("site") or cfg["site"]
        self.group = self.state.get("group") or cfg["group"]
        self.label = self.state.get("label") or cfg["label"] or ""
        if not self.state.get("uuid"):
            self.state["uuid"] = str(uuidlib.uuid4())
        self.state["boot_count"] = int(self.state.get("boot_count", 0)) + 1
        save_state(cfg["state_file"], self.state)
        self.uuid = uuidlib.UUID(self.state["uuid"]).bytes
        self.interval = max(2, int(float(cfg["interval_s"])))
        self.server, self.server_via = "", ""
        self.mqtt = None
        self.mqtt_ok = False
        self.mqtt_fails = 0
        self.reconnects = 0
        self.use_http = paho is None
        self.tx_ok = self.tx_fail = 0
        self.seq = 0
        self.ev_seq = 0
        self.batch_seq = 0
        self.min_free: int | None = None
        self.stop = threading.Event()
        self.led = Led(find_led(cfg.get("led", "")))
        self.alert_led = Led(find_led(cfg.get("alert_led", ""))) if cfg.get("alert_led") else self.led
        self.alert_hold = max(1.0, float(cfg.get("alert_hold_s", "20")))
        self._alert_until = 0.0
        self._lease: dict[int, float] = {}
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._seen_rids: OrderedDict[int, None] = OrderedDict()
        self._cancelled: set[int] = set()
        self.started = time.monotonic()
        self.dns: nob.NetObserve | None = None
        if cfg.get("dns_mode", "off").strip().lower() == "forward":
            ups = [u.strip() for u in cfg.get("dns_upstream", "").split(",") if u.strip()] or list(nob.DEFAULT_UPSTREAMS)
            self.dns = nob.NetObserve(upstreams=ups, port=int(cfg.get("dns_port", "53")),
                                      bind=cfg.get("dns_bind", ""), on_flag=self.on_flag)
            if isinstance(self.state.get("watchlist"), dict):
                self.dns.watchlist.restore(self.state["watchlist"])
        self.flush_s = max(2.0, float(cfg.get("dns_flush_s", "10")))
        self._last_flush = time.monotonic()

    # -- topics ------------------------------------------------------------------
    def topic(self, ch: str) -> str:
        return f"{P.TOPIC_ROOT}/{self.site}/{self.hwid}/{ch}"

    # -- bodies (named -> integer-keyed CBOR maps) ----------------------------------
    def tools(self) -> list:
        ro = P.TOOL_FLAG_READ_ONLY | P.TOOL_FLAG_IDEMPOTENT
        t = [[P.TOOL_GET_DEVICE_INFO, 1, ro], [P.TOOL_READ_TELEMETRY, 1, ro], [P.TOOL_IDENTIFY, 1, 0],
             [P.TOOL_REBOOT, 1, P.TOOL_FLAG_DESTRUCTIVE]]
        if self.alert_led.path:
            t.append([P.TOOL_SET_ACTUATOR, 1, P.TOOL_FLAG_DESTRUCTIVE])
        if self.dns:
            t += [[P.TOOL_SET_WATCHLIST, 1, P.TOOL_FLAG_IDEMPOTENT], [P.TOOL_GET_WATCHLIST, 1, ro],
                  [P.TOOL_GET_NETWORK_STATS, 1, ro], [P.TOOL_GET_LOCAL_DEVICES, 1, ro],
                  [P.TOOL_QUERY_DNS_LOG, 1, ro], [P.TOOL_SCAN_LAN, 1, 0], [P.TOOL_FLUSH_DNS_CACHE, 1, P.TOOL_FLAG_IDEMPOTENT]]
        return sorted(t)

    def hello(self) -> dict:
        k = cbor.keyed
        actuators = []
        if self.alert_led.path:
            actuators.append(k("ActuatorDescriptor", {"ch": 0, "name": "alert_led", "kind": P.ACT_GPIO,
                                                      "gpio": 0, "safe": 0}))
        net = None
        if self.dns:
            wl = self.dns.watchlist.snapshot()
            net = k("NetObserveInfo", {
                "mode": P.DNS_MODE_FORWARD if self.dns.running else P.DNS_MODE_OFF,
                "port": self.dns.port, "upstreams": [socket.inet_aton(u) for u in self.dns.upstreams],
                "caps": self.dns.caps, "watchVer": wl["version"], "watchRules": len(wl["rules"])})
        sha = hashlib.sha256(Path(__file__).read_bytes()).digest()[:8]
        return k("HelloBody", {
            "proto": [P.VERSION, P.VERSION], "hwid": self.hwid, "uuid": self.uuid,
            "fw": k("FirmwareInfo", {"version": FW_VERSION, "elfSha": sha,
                                     "idf": f"py{platform.python_version()}"[:16]}),
            "chip": k("ChipInfo", {"model": P.CHIP_OTHER, "rev": 0, "cores": min(os.cpu_count() or 1, 255),
                                   "flashMb": 0, "psramMb": 0}),
            "limits": k("LinkLimits", {"maxDownlink": P.LIMITS["downlinkFrame"], "maxUplink": UPLINK_MAX,
                                       "maxInflight": MAX_INFLIGHT}),
            "tools": self.tools(), "models": [], "actuators": actuators,
            "metrics": [list(m) for m in EXT_METRICS], "tasks": [],
            "mode": P.MODE_ACTIVE, "site": self.site, "group": self.group,
            "label": self.label[:48] or None,
            "resetReason": P.RESET_SOFTWARE if self.state["boot_count"] > 1 else P.RESET_POWER_ON,
            "bootCount": self.state["boot_count"] & 0xFFFFFFFF, "net": net,
        })

    def status_body(self, state: int) -> dict:
        ip = local_ip(self.server or "1.1.1.1")
        return cbor.keyed("StatusBody", {"state": state, "mode": P.MODE_ACTIVE,
                                         "ip": socket.inet_aton(ip) if ip else None})

    def telemetry_body(self, sections: int = P.TM_HEAP | P.TM_NET | P.TM_CPU | P.TM_SENSORS | P.TM_EXT) -> dict:
        k = cbor.keyed
        m = collect_metrics()
        body: dict = {"seq": self.seq & 0xFFFFFFFF, "uptimeS": uptime_s() & 0xFFFFFFFF,
                      "unixS": int(time.time()) & 0xFFFFFFFF, "mode": P.MODE_ACTIVE}
        self.seq += 1
        if sections & P.TM_HEAP and "free_heap_bytes" in m:
            free = min(m["free_heap_bytes"], 0xFFFFFFFF)
            self.min_free = free if self.min_free is None else min(self.min_free, free)
            body["heap"] = k("HeapStats", {"internalFree": free, "internalMinFree": self.min_free,
                                           "internalLargest": free})
        if sections & P.TM_NET:
            rssi = m.get("rssi_dbm")
            body["net"] = k("NetStats", {"rssiDbm": int(max(-128, min(127, rssi))) if rssi is not None else None,
                                         "txFail": self.tx_fail & 0xFFFFFFFF,
                                         "reconnects": min(self.reconnects, 0xFFFF), "uplinkQueued": 0})
        if sections & P.TM_CPU and "cpu_pct" in m:
            body["cpu"] = [int(max(0, min(100, round(m["cpu_pct"]))))]
        if sections & P.TM_SENSORS and "temp_c" in m:
            body["sensors"] = k("SensorStats", {"dieTempC": float(m["temp_c"])})
        if sections & P.TM_ACTUATORS and self.alert_led.path:
            body["actuators"] = [self.alert_led.value]
        if sections & P.TM_EXT:
            ext = {mid: float(m[name]) for mid, name, _u in EXT_METRICS if name in m}
            if ext:
                body["ext"] = ext
        if self.dns:
            body["dns"] = k("DnsCounters", self.dns.counters())
        return k("TelemetryBody", body)

    # -- server discovery: config -> mDNS -> fallback (as the ESP32 does) --------------
    def resolve_server(self) -> None:
        if self.cfg["server"]:
            self.server, self.server_via = self.cfg["server"], "config"
            return
        try:
            infos = socket.getaddrinfo(self.cfg["mdns_host"], None, socket.AF_INET)
            self.server, self.server_via = infos[0][4][0], "mdns"
        except OSError:
            self.server, self.server_via = self.cfg["server_fallback"], "fallback"
        log.info("server = %s (via %s)", self.server or "(none)", self.server_via)

    # -- uplink ---------------------------------------------------------------------
    def publish(self, ch: str, frame: bytes, *, retain: bool = False) -> bool:
        if len(frame) > UPLINK_MAX:
            log.warning("dropping %d-byte %s frame (limit %d)", len(frame), ch, UPLINK_MAX)
            return False
        if self.mqtt and self.mqtt_ok:
            qos = P.CHANNEL_POLICY[ch]["qos"]
            ok = self.mqtt.publish(self.topic(ch), frame, qos=qos, retain=retain).rc == 0
        elif self.use_http:
            ok = self.send_http(ch, frame)
        else:
            ok = False
        self.tx_ok += ok
        self.tx_fail += not ok
        return ok

    def send_http(self, ch: str, frame: bytes) -> bool:
        if not self.server:
            return False
        url = f"http://{self.server}:{self.cfg['http_port']}/api/v1/ewp/{self.hwid}/{ch}?site={self.site}"
        req = urllib.request.Request(url, frame, method="POST",
                                     headers={"Content-Type": "application/octet-stream",
                                              "X-Fleet-Token": self.cfg["fleet_token"]})
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                return 200 <= r.status < 300
        except Exception as e:                     # noqa: BLE001
            log.warning("HTTP ingest failed: %s", e)
            return False

    def send_hello_status(self) -> None:
        self.publish("hello", cbor.encode_frame(P.MSG_HELLO, self.hello()), retain=True)
        self.publish("st", cbor.encode_frame(P.MSG_STATUS, self.status_body(P.STATE_ONLINE)), retain=True)

    def send_event(self, body: dict, flags: int = 0) -> bool:
        self.ev_seq += 1
        return self.publish("ev", cbor.encode_frame(P.MSG_EVENT, body, rid=self.ev_seq, flags=flags))

    def flush_dns(self) -> int:
        """DNS_BATCH events for everything recorded since the previous flush."""
        if not self.dns:
            return 0
        now = time.monotonic()
        window_ms = int((now - self._last_flush) * 1000)
        self._last_flush = now
        batch, dropped = self.dns.take_batch()
        if not batch and not dropped:
            return 0
        chunks = self.dns.batch_frames(batch, now=now, max_body=UPLINK_MAX - P.HEADER_LEN - 64) or [([], [])]
        self.batch_seq += 1
        sent = 0
        for i, (clients, queries) in enumerate(chunks):
            dns = cbor.keyed("DnsBatch", {"seq": self.batch_seq & 0xFFFFFFFF, "windowMs": min(window_ms, 0xFFFFFFFF),
                                          "clients": clients, "queries": queries,
                                          "dropped": (dropped or None) if i == 0 else None,
                                          "more": True if i < len(chunks) - 1 else None})
            sent += self.send_event(cbor.keyed("EventBody", {"kind": P.EVENT_DNS_BATCH, "dns": dns}))
        return sent

    def on_flag(self, f: dict) -> None:
        """Called by the forwarder thread on an ALERT rule match."""
        age = int((time.monotonic() - f["t"]) * 1000)
        flag = cbor.keyed("DnsFlag", {
            "client": [socket.inet_aton(f["ip"]), f["mac"], f["hostname"]], "qname": f["qname"],
            "qtype": f["qtype"], "category": f["category"], "rule": f["rule"], "ageMs": age,
            "watchVer": f["watchVer"] & 0xFFFFFFFF})
        self.send_event(cbor.keyed("EventBody", {"kind": P.EVENT_DNS_FLAGGED, "flag": flag}))
        log.info("FLAG %s (%s) asked for %s [%s]", f["ip"], f["hostname"] or "?", f["qname"], f["rule"])
        if self.alert_led.path:
            self._alert_until = time.monotonic() + self.alert_hold
            self.alert_led.set(True)

    # -- downlink -------------------------------------------------------------------
    def on_downlink(self, payload: bytes) -> None:
        try:
            hdr, body = cbor.decode_frame(payload, max_len=P.LIMITS["downlinkFrame"])
        except cbor.FrameError as e:
            self.reply_error({"rid": 0, "tool": P.NO_TOOL}, P.ST_BAD_FRAME, str(e))
            return
        if hdr["type"] == P.MSG_CANCEL:
            self._cancelled.add(hdr["rid"])
            return
        if hdr["type"] != P.MSG_REQ:
            return                                  # only REQ / CANCEL belong on dn
        if hdr["tool"] not in {t[0] for t in self.tools()}:
            self.reply_error(hdr, P.ST_UNKNOWN_TOOL, f"tool 0x{hdr['tool']:02x} is not on this node")
            return
        if hdr["rid"] in self._seen_rids:
            self.reply_error(hdr, P.ST_DUPLICATE, "duplicate request id")
            return
        self._seen_rids[hdr["rid"]] = None
        while len(self._seen_rids) > DEDUPE:
            self._seen_rids.popitem(last=False)
        with self._inflight_lock:
            if self._inflight >= MAX_INFLIGHT:
                self.reply_error(hdr, P.ST_BUSY, "node busy, retry later", retry_after_ms=250)
                return
            self._inflight += 1
        received = time.monotonic()
        if hdr["flags"] & P.FLAG_ACK_REQUESTED:
            self.publish("rs", cbor.encode_frame(P.MSG_ACK, cbor.keyed("AckBody", {"queueDepth": self._inflight}),
                                                 rid=hdr["rid"], tool=hdr["tool"], status=P.ST_ACCEPTED))
        threading.Thread(target=self._run_tool, args=(hdr, body, received), daemon=True,
                         name=f"tool-{hdr['tool']:02x}").start()

    def _run_tool(self, hdr: dict, body, received: float) -> None:
        try:
            if hdr["flags"] & P.FLAG_BROADCAST:
                time.sleep(random.uniform(0, 2.0))          # no reply storm from the whole fleet
            deadline = received + (hdr["deadline_ms"] or P.LIMITS["defaultDeadlineMs"]) / 1000.0
            if hdr["rid"] in self._cancelled:
                self._cancelled.discard(hdr["rid"])
                raise ToolError(P.ST_CANCELLED, "cancelled by the host")
            if time.monotonic() > deadline:
                raise ToolError(P.ST_DEADLINE_EXCEEDED, "deadline passed before the tool ran")
            t0 = time.monotonic()
            after, result = self.execute(hdr["tool"], body if isinstance(body, dict) else {})
            timing = cbor.keyed("ExecTiming", {"queueUs": int((t0 - received) * 1e6) & 0xFFFFFFFF,
                                               "execUs": int((time.monotonic() - t0) * 1e6) & 0xFFFFFFFF,
                                               "heapDelta": 0})
            frame = cbor.encode_frame(P.MSG_RES, cbor.keyed("ResultBody", {"timing": timing, "result": result}),
                                      rid=hdr["rid"], tool=hdr["tool"], status=P.ST_OK)
            if len(frame) > UPLINK_MAX:
                raise ToolError(P.ST_TOO_LARGE, "result does not fit one uplink frame - ask for fewer entries")
            self.publish("rs", frame)
            if after:
                after()
        except ToolError as e:
            self.reply_error(hdr, e.status, e.detail, e.arg_key, e.retry_after_ms)
        except Exception as e:                              # noqa: BLE001 - the node must answer
            log.exception("tool 0x%02x failed", hdr["tool"])
            self.reply_error(hdr, P.ST_TOOL_FAULT, f"{type(e).__name__}: {e}"[:96])
        finally:
            with self._inflight_lock:
                self._inflight -= 1

    def reply_error(self, hdr: dict, status: int, detail: str, arg_key: int | None = None,
                    retry_after_ms: int | None = None) -> None:
        body = cbor.keyed("ErrorBody", {"detail": detail[:96], "argKey": arg_key, "retryAfterMs": retry_after_ms})
        self.publish("rs", cbor.encode_frame(P.MSG_ERR, body, rid=hdr.get("rid", 0), tool=hdr.get("tool", 0),
                                             status=status))

    # -- tools ------------------------------------------------------------------------
    def execute(self, tool: int, a: dict):
        """-> (after-action or None, result body). Raises ToolError."""
        K = P.KEYS
        if tool == P.TOOL_GET_DEVICE_INFO:
            return None, self.hello()
        if tool == P.TOOL_READ_TELEMETRY:
            sec = a.get(K["ReadTelemetryArgs"]["sections"],
                        P.TM_HEAP | P.TM_NET | P.TM_CPU | P.TM_ACTUATORS)
            return None, self.telemetry_body(_u(sec, 0, 255, 1))
        if tool == P.TOOL_IDENTIFY:
            ms = _u(a.get(K["IdentifyArgs"]["durationMs"], 1200), 100, 10_000, 1)
            if not self.led.blink(times=max(2, ms // 120)):
                raise ToolError(P.ST_HW_FAULT, "no controllable LED (the agent needs root for sysfs LEDs)")
            return None, {}
        if tool == P.TOOL_REBOOT:
            delay = _u(a.get(K["RebootArgs"]["delayMs"], 500), 0, 10_000, 1)

            def reboot():
                time.sleep(delay / 1000.0)
                subprocess.run(["systemctl", "reboot"], check=False)
            return reboot, cbor.keyed("RebootResult", {"inMs": delay})
        if tool == P.TOOL_SET_ACTUATOR:
            return None, self.set_actuator(a)
        if not self.dns:
            raise ToolError(P.ST_NOT_READY, "the DNS forwarder is off (dns_mode = forward)")
        if tool == P.TOOL_SET_WATCHLIST:
            ka = K["SetWatchlistArgs"]
            try:
                ver, n_rules, n_cats = self.dns.watchlist.apply(a.get(ka["op"]), a.get(ka["version"]),
                                                                a.get(ka["rules"]) or [], a.get(ka["categories"]))
            except OverflowError as e:
                raise ToolError(P.ST_TOO_LARGE, str(e), ka["rules"]) from e
            except ValueError as e:
                raise ToolError(P.ST_INVALID_ARGS, str(e)[:96], ka["rules"]) from e
            self.state["watchlist"] = self.dns.watchlist.snapshot()
            save_state(self.cfg["state_file"], self.state)
            # The retained HELLO must show the new version, or the host would push it again.
            threading.Thread(target=lambda: self.publish("hello", cbor.encode_frame(P.MSG_HELLO, self.hello()),
                                                         retain=True), daemon=True).start()
            return None, cbor.keyed("WatchlistResult", {"version": ver, "rules": n_rules, "categories": n_cats})
        if tool == P.TOOL_GET_WATCHLIST:
            s = self.dns.watchlist.snapshot()
            return None, cbor.keyed("GetWatchlistResult", s)
        if tool == P.TOOL_GET_NETWORK_STATS:
            ago = int((time.monotonic() - self.dns.last_client_query) * 1000) if self.dns.last_client_query else None
            return None, cbor.keyed("NetworkStatsResult", {
                "dns": cbor.keyed("DnsCounters", self.dns.counters()), "upstreams": self.dns.upstream_rows(),
                "cacheEntries": min(len(self.dns.cache), 0xFFFF), "pending": min(len(self.dns.pending), 0xFFFF),
                "lastClientQueryAgoMs": min(ago, 0xFFFFFFFF) if ago is not None else None})
        if tool == P.TOOL_GET_LOCAL_DEVICES:
            limit = _u(a.get(K["GetLocalDevicesArgs"]["limit"], P.LIMITS["localDevices"]), 1,
                       P.LIMITS["localDevices"], 1)
            rows, total = self.dns.clients.rows(limit)
            return None, cbor.keyed("LocalDevicesResult", {"devices": rows, "total": total})
        if tool == P.TOOL_QUERY_DNS_LOG:
            kq = K["QueryDnsLogArgs"]
            limit = _u(a.get(kq["limit"], 16), 1, P.LIMITS["dnsLogQuery"], kq["limit"])
            client = a.get(kq["client"])
            if client is not None and (not isinstance(client, bytes) or len(client) != 4):
                raise ToolError(P.ST_INVALID_ARGS, "client must be 4 bytes (IPv4)", kq["client"])
            clients, queries = self.dns.query_log(limit, socket.inet_ntoa(client) if client else None,
                                                  bool(a.get(kq["flaggedOnly"], False)))
            return None, cbor.keyed("DnsLogResult", {"clients": clients, "queries": queries})
        if tool == P.TOOL_SCAN_LAN:
            n, eta = self.dns.scan_lan()
            if n == 0:
                raise ToolError(P.ST_NOT_READY, "no private IPv4 network to scan")
            return None, cbor.keyed("ScanLanResult", {"targets": min(n, 0xFFFF), "etaMs": eta})
        if tool == P.TOOL_FLUSH_DNS_CACHE:
            return None, cbor.keyed("FlushDnsCacheResult", {"flushed": min(self.dns.flush_cache(), 0xFFFF)})
        raise ToolError(P.ST_UNKNOWN_TOOL, "unknown tool")

    def set_actuator(self, a: dict) -> dict:
        ka = P.KEYS["SetActuatorArgs"]
        ch = _u(a.get(ka["channel"]), 0, 0, ka["channel"])
        v = a.get(ka["value"])
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v not in (0, 1):
            raise ToolError(P.ST_INVALID_ARGS, "alert_led is on/off: value must be 0 or 1", ka["value"])
        lease = a.get(ka["leaseMs"])
        if lease is not None:
            lease = _u(lease, 1, P.LIMITS["leaseMaxMs"], ka["leaseMs"])
        prev = self.alert_led.value
        if not self.alert_led.set(bool(v)):
            raise ToolError(P.ST_HW_FAULT, "LED not writable (the agent needs root for sysfs LEDs)")
        if lease:
            self._lease[ch] = time.monotonic() + lease / 1000.0
        else:
            self._lease.pop(ch, None)
        self._alert_until = 0.0                    # an explicit setting wins over the alarm hold
        return cbor.keyed("SetActuatorResult", {"channel": ch, "applied": float(v), "previous": prev,
                                                "leaseRemainingMs": lease})

    def housekeeping(self) -> None:
        now = time.monotonic()
        if self._alert_until and now >= self._alert_until:
            self._alert_until = 0.0
            self.alert_led.set(False)
        for ch, until in list(self._lease.items()):
            if now >= until:
                del self._lease[ch]
                self.alert_led.set(False)
                self.send_event(cbor.keyed("EventBody", {"kind": P.EVENT_LEASE_EXPIRED, "channel": ch,
                                                         "revertedTo": 0.0}))

    # -- MQTT -------------------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        ok = (rc == 0) if isinstance(rc, int) else not rc.is_failure
        if not ok:
            log.warning("MQTT connect refused: %s", rc)
            return
        if self.mqtt_ok is False and self.tx_ok:
            self.reconnects += 1
        self.mqtt_ok, self.mqtt_fails, self.use_http = True, 0, False
        client.subscribe([(self.topic("dn"), 1), (P.BROADCAST_TOPIC, 1)])
        self.send_hello_status()
        log.info("MQTT connected -> %s:%s", self.server, self.cfg["mqtt_port"])

    def _on_disconnect(self, client, userdata, *args):
        if self.mqtt_ok:
            log.warning("MQTT disconnected; paho reconnects, HTTP carries frames meanwhile")
        self.mqtt_ok = False

    def _on_message(self, client, userdata, msg):
        self.on_downlink(bytes(msg.payload))

    def start_mqtt(self) -> None:
        if paho is None:
            log.warning("paho-mqtt not installed (apt install python3-paho-mqtt) - HTTP only, no commands")
            return
        try:
            c = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2,
                            client_id=self.hwid, clean_session=True)
        except AttributeError:                     # paho < 2.0 (Bullseye apt package)
            c = paho.Client(client_id=self.hwid, clean_session=True)
        if self.cfg["mqtt_username"]:
            c.username_pw_set(self.cfg["mqtt_username"], self.cfg["mqtt_password"])
        c.will_set(self.topic("st"), cbor.encode_frame(P.MSG_STATUS, cbor.keyed("StatusBody", {"state": P.STATE_OFFLINE})),
                   qos=1, retain=True)
        c.on_connect, c.on_disconnect, c.on_message = self._on_connect, self._on_disconnect, self._on_message
        c.reconnect_delay_set(min_delay=2, max_delay=60)
        c.connect_async(self.server, int(self.cfg["mqtt_port"]), keepalive=60)
        c.loop_start()
        self.mqtt = c

    def tick(self) -> None:
        """One telemetry interval: broker fallback bookkeeping + TELEMETRY."""
        if not (self.mqtt and self.mqtt_ok):
            if self.mqtt:
                self.mqtt_fails += 1
                if self.mqtt_fails >= MQTT_FAIL_THRESHOLD and not self.use_http:
                    log.warning("no broker after %d tries - falling back to HTTP ingest", self.mqtt_fails)
                    self.use_http = True
                    self.send_hello_status()
            if self.use_http and self.mqtt_fails % 5 == 0:
                self.resolve_server()              # the server may have moved
        self.publish("tm", cbor.encode_frame(P.MSG_TELEMETRY, self.telemetry_body()))

    # -- lifecycle ----------------------------------------------------------------------
    def run(self) -> None:
        log.info("Ionity agent %s | %s | %s | site/group %s/%s | DNS %s", FW_VERSION, self.hwid,
                 self.product, self.site, self.group, "forward" if self.dns else "off")
        if self.dns and not self.dns.start():
            log.error("DNS forwarder disabled: %s", self.dns.bind_error)
        self.resolve_server()
        if self.use_http:
            self.send_hello_status()
        self.start_mqtt()
        time.sleep(1.5)                            # give MQTT a moment before the first reading
        next_tm = 0.0
        while not self.stop.is_set():
            now = time.monotonic()
            if now >= next_tm:
                self.tick()
                next_tm = now + self.interval
            if self.dns and now - self._last_flush >= self.flush_s:
                self.flush_dns()
            self.housekeeping()
            self.stop.wait(0.5)
        self.shutdown()

    def shutdown(self) -> None:
        if self.dns:
            self.flush_dns()
            self.dns.stop()
        if self.alert_led.path:
            self.alert_led.set(False)
        if self.mqtt:
            try:
                if self.mqtt_ok:
                    self.mqtt.publish(self.topic("st"), cbor.encode_frame(
                        P.MSG_STATUS, cbor.keyed("StatusBody", {"state": P.STATE_OFFLINE})),
                        qos=1, retain=True).wait_for_publish(2)
                self.mqtt.disconnect()
            finally:
                self.mqtt.loop_stop()
        log.info("stopped")


def _readable(v):
    """Decoded CBOR -> something json.dumps can print (bytes as hex)."""
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, dict):
        return {str(k): _readable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_readable(x) for x in v]
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ionity fleet agent 2 (Raspberry Pi / Linux, EWP/1)")
    ap.add_argument("--config", default=os.environ.get("IONITY_AGENT_CONFIG", DEFAULT_CONF))
    ap.add_argument("--once", action="store_true", help="print HELLO and one TELEMETRY (decoded) and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    agent = Agent(load_config(a.config))
    if a.once:
        agent.resolve_server()
        cpu_pct()                                  # prime the CPU delta
        time.sleep(0.3)
        out = {"hwid": agent.hwid, "topic": agent.topic("tm"),
               "hello": {P.NAMES["HelloBody"].get(k, k): _readable(v) for k, v in agent.hello().items()},
               "telemetry": {P.NAMES["TelemetryBody"].get(k, k): _readable(v)
                             for k, v in agent.telemetry_body().items()}}
        print(json.dumps(out, indent=2))
        return 0
    signal.signal(signal.SIGTERM, lambda *_: agent.stop.set())
    signal.signal(signal.SIGINT, lambda *_: agent.stop.set())
    agent.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
