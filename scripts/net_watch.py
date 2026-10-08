#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Network watch: "YouTube alarm" proof of concept
Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd | AEDI
Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
Owner: github.com/Ionity-Global-Pty-Ltd | www.ionity.today | ai@ionity.today

Watches the fleet server's own LAN DNS log (through MCP). The moment any device
on the network looks up a watched site (default: YouTube), it switches on a red
LED on the ESP32 (set_actuator over MCP) and keeps a live report of where every
device went. The light goes off HOLD seconds after the last hit.

How it sees traffic: devices must use the fleet server (192.168.124.2) as DNS
(router DHCP DNS for the whole house, or static DNS on one phone; Android
"Private DNS" and browser "secure DNS" off). The ESP32 cannot read other
devices' WiFi traffic; it is the alarm light + edge node.

Wiring: red LED + 220 ohm resistor from the board's pwm0 pin to GND
(lab-node-01: GPIO5). pwm0 is not touched by the firmware's own loops.

    .venv\\Scripts\\python.exe scripts\\net_watch.py
        [--device esp32-98a316e5d18c] [--channel pwm0] [--hold 20]
        [--watch youtube.com,googlevideo.com,...] [--ignore 192.168.124.2]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCH = ("youtube.com,youtu.be,googlevideo.com,ytimg.com,youtube-nocookie.com,"
                 "youtubei.googleapis.com,yt3.ggpht.com")
LOG = ROOT / "logs" / "net_watch.log"


def env_value(name: str) -> str:
    v = os.environ.get(name, "")
    p = ROOT / ".env"
    if not v and p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            s = line.strip()
            if "=" in s and s.split("=", 1)[0].strip() == name:
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    return v


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
    print(line, flush=True)
    try:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


class Mcp:
    def __init__(self, url: str, token: str):
        self.url, self.token, self.n = url, token, 0

    def call(self, tool: str, args: dict | None = None, timeout: float = 15):
        self.n += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.n, "method": "tools/call",
                           "params": {"name": tool, "arguments": args or {}}}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8", "replace"))
        if "error" in resp:
            raise RuntimeError(resp["error"])
        res = resp.get("result") or {}
        data = res.get("structuredContent")
        if data is None:
            txt = next((c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"), "")
            try:
                data = json.loads(txt)
            except ValueError:
                data = txt
        if res.get("isError"):
            raise RuntimeError(data)
        return data


def find_rows(x, key: str) -> list:
    """First list of dicts carrying `key`, anywhere inside x."""
    if isinstance(x, list) and x and isinstance(x[0], dict) and key in x[0]:
        return x
    for v in (x.values() if isinstance(x, dict) else x if isinstance(x, list) else []):
        r = find_rows(v, key)
        if r:
            return r
    return []


def matched(qname: str, watch: list[str]) -> str | None:
    q = qname.lower().rstrip(".")
    return next((w for w in watch if q == w or q.endswith("." + w)), None)


def write_report(path: Path, mcp: Mcp, minutes: int, events: list, light_on: bool, a) -> None:
    devs = find_rows(mcp.call("dns_by_device", {"minutes": minutes, "limit": 50}), "client_ip")
    esc = html.escape
    ev_rows = "".join(
        f"<tr><td>{time.strftime('%H:%M:%S', time.localtime(e['ts']))}</td><td>{esc(e['label'])}</td>"
        f"<td>{esc(e['ip'])}</td><td class=hit>{esc(e['qname'])}</td></tr>" for e in reversed(events[-50:]))
    dev_rows = ""
    for d in devs:
        doms = d.get("top_domains") or d.get("domains") or []
        chips = " ".join(
            f"<span class='chip{' hot' if matched(str(x.get('qname', x) if isinstance(x, dict) else x), a.watch) else ''}'>"
            f"{esc(str(x.get('qname', x) if isinstance(x, dict) else x))}"
            f"{(' &times;' + str(x.get('count'))) if isinstance(x, dict) and x.get('count') else ''}</span>"
            for x in doms)
        dev_rows += (f"<tr><td><b>{esc(str(d.get('label') or d.get('hostname') or '-'))}</b><br>"
                     f"<small>{esc(str(d.get('vendor') or ''))}</small></td><td>{esc(str(d.get('client_ip')))}</td>"
                     f"<td>{esc(str(d.get('queries', '')))}</td><td>{chips}</td></tr>")
    state = ("<span class=on>&#9679; RED LIGHT ON</span>" if light_on else "<span class=off>&#9675; light off</span>")
    page = f"""<!doctype html><html><head><meta charset=utf-8><meta http-equiv=refresh content=5>
<title>Ionity network watch</title><style>
body{{background:#0b121c;color:#e6ecf2;font:15px Segoe UI,Arial,sans-serif;margin:24px}}
h1{{font-size:22px;margin:0}} h2{{font-size:16px;color:#1a9bc4;margin:22px 0 8px}}
table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid #22324a;padding:7px 9px;text-align:left;vertical-align:top}}
th{{background:#0d2137;color:#fff;font-size:13px}} small{{color:#8c9bab}}
.chip{{display:inline-block;background:#142235;border:1px solid #22324a;border-radius:10px;padding:1px 8px;margin:2px;font-size:12px}}
.hot{{background:#4a1515;border-color:#ff6e6e;color:#ffd0d0}} .hit{{color:#ff8a8a;font-weight:600}}
.on{{color:#ff5050;font-weight:700}} .off{{color:#8c9bab}} .bar{{display:flex;gap:28px;align-items:center;margin-top:6px;color:#8c9bab}}
</style></head><body>
<h1>Ionity network watch &mdash; live report</h1>
<div class=bar><div>{state} on {esc(a.device)} ({esc(a.channel)})</div><div>watching: {esc(', '.join(a.watch[:4]))}&hellip;</div>
<div>updated {time.strftime('%Y-%m-%d %H:%M:%S')} SAST</div></div>
<h2>Alarms &mdash; watched sites opened</h2><table><tr><th>Time</th><th>Device</th><th>IP</th><th>Site</th></tr>
{ev_rows or '<tr><td colspan=4><small>none yet</small></td></tr>'}</table>
<h2>Where each device went (last {minutes} min, from the fleet server's DNS log)</h2>
<table><tr><th>Device</th><th>IP</th><th>Lookups</th><th>Sites</th></tr>{dev_rows or '<tr><td colspan=4><small>no lookups yet</small></td></tr>'}</table>
<p><small>DOC-2026-10-ESP32MCP-NETWATCH &middot; Policy 986 AED &middot; &copy; 2018-2026 Antwerp Designs | Ionity (Pty) Ltd &middot; ionity.today</small></p>
</body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(page, encoding="utf-8")
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8099")
    ap.add_argument("--device", default=(env_value("IONITY_ALARM_DEVICE") or "esp32-98a316e5d18c"),
                    help="board that shows the red light (default: IONITY_ALARM_DEVICE in .env)")
    ap.add_argument("--channel", default="pwm0", choices=["pwm0", "pwm1", "relay0", "alert_led", "led"])
    ap.add_argument("--hold", type=float, default=20, help="seconds the light stays on after the last hit")
    ap.add_argument("--poll", type=float, default=1.5)
    ap.add_argument("--watch", default=DEFAULT_WATCH)
    ap.add_argument("--ignore", default="", help="client IPs to ignore, comma separated")
    ap.add_argument("--minutes", type=int, default=60, help="report window")
    ap.add_argument("--report", default=str(ROOT / "Demo working" / "network-report.html"))
    a = ap.parse_args()
    a.watch = [w.strip().lower() for w in a.watch.split(",") if w.strip()]
    ignore = {x.strip() for x in a.ignore.split(",") if x.strip()}
    mcp = Mcp(a.base.rstrip("/") + "/api/v1/mcp/rpc", env_value("IONITY_ADMIN_TOKEN"))
    report = Path(a.report)
    key = lambda r: (r.get("ts"), r.get("client_ip"), r.get("qname"), r.get("qtype"))
    seen = {key(r) for r in find_rows(mcp.call("dns_recent", {"limit": 200}), "qname")}
    events: list = []
    labels: dict = {}
    state = {"on": False, "last_hit": 0.0}
    last_report = last_labels = 0.0

    def set_light(on: bool) -> None:
        try:
            r = mcp.call("device_call_tool", {"device_id": a.device, "tool": "set_actuator",
                                              "arguments": {"channel": a.channel, "value": 1 if on else 0}}, timeout=25)
            state["on"] = on
            log(f"LED {a.channel} on {a.device} -> {'ON (red)' if on else 'off'}   board said: {json.dumps(r)[:110]}")
        except Exception as e:  # noqa: BLE001
            log(f"LED command FAILED: {e}")

    log(f"net_watch up: device={a.device} channel={a.channel} hold={a.hold}s watch={','.join(a.watch)}")
    log(f"report -> {report}   (open it in a browser; refreshes every 5 s)")
    set_light(False)
    try:
        while True:
            try:
                now = time.time()
                if now - last_labels > 30:
                    for d in find_rows(mcp.call("list_lan_devices", {}), "ip"):
                        labels[d.get("ip")] = d.get("label") or d.get("hostname") or d.get("vendor") or ""
                    last_labels = now
                rows = find_rows(mcp.call("dns_recent", {"limit": 100}), "qname")
                for r in sorted(rows, key=lambda r: r.get("ts") or 0):
                    k = key(r)
                    if k in seen:
                        continue
                    seen.add(k)
                    ip = str(r.get("client_ip", "?"))
                    m = matched(str(r.get("qname", "")), a.watch)
                    if ip in ignore or not m:
                        continue
                    lab = r.get("hostname") or labels.get(ip) or ip
                    events.append({"ts": r.get("ts") or now, "ip": ip, "label": str(lab), "qname": str(r.get("qname"))})
                    state["last_hit"] = time.time()
                    log(f"ALARM  {ip} ({lab}) opened {r.get('qname')}   [watch: {m}]")
                    if not state["on"]:
                        set_light(True)
                if state["on"] and time.time() - state["last_hit"] > a.hold:
                    set_light(False)
                if time.time() - last_report > 5:
                    write_report(report, mcp, a.minutes, events, state["on"], a)
                    last_report = time.time()
            except Exception as e:  # noqa: BLE001
                log(f"poll error: {e}")
            time.sleep(a.poll)
    except KeyboardInterrupt:
        log("stopping: light off")
        set_light(False)


if __name__ == "__main__":
    main()
