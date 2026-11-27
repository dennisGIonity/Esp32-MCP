#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | ESP32-MCP v3 live board check (run by a person, with real boards)
Doc ID: DOC-2026-11-ESP32MCP-LIVECHECK | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Walks the boardroom proof of concept against a RUNNING fleet host and real
boards, one step at a time, and records PASS / FAIL / SKIP with evidence:

  1. host health          MQTT connected, nothing degraded
  2. EWP/1 nodes          firmware 3.2 / Pi agent 2.1 online, offering ping_host
  3. ping_from_node       the board pings the router and a public resolver
  4. DNS through the node you point a phone at the node as DNS, open YouTube
  5. flag + red LED       the YouTube flag reaches the host; you confirm the LED
  6. MQTT back-off        you pull the broker; the board comes back by itself

It changes NOTHING by itself: no router or DHCP settings, no port 53 binding,
no flashing, no reboots. Steps that need the router, the phone or the broker
are done by YOU, and the script waits for you (Enter) or skips (s).

    python tools/scripts/live_check.py                       # http://127.0.0.1:8099
    python tools/scripts/live_check.py --base http://192.168.0.10:8099 --node esp32-98a316e5d18c
    IONITY_ADMIN_TOKEN=... python tools/scripts/live_check.py --gateway 192.168.0.1

Exit code 0 only when nothing FAILED. A report is written to live_check_<time>.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

RESULTS: list[dict] = []


def rec(status: str, name: str, evidence: str) -> None:
    RESULTS.append({"status": status, "step": name, "evidence": evidence})
    print(f"  [{status}] {name}: {evidence}")


class Host:
    def __init__(self, base: str, token: str):
        self.base, self.token = base.rstrip("/"), token

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 70) -> dict:
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return {"ok": False, "http_status": e.code, "error": e.read().decode(errors="replace")[:300]}


def ask(prompt: str) -> bool:
    """True = go on, False = skip this step. Ctrl+C stops the whole check."""
    a = input(f"\n>>> {prompt}\n    Enter when done, s to skip: ").strip().lower()
    return a != "s"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.getenv("IONITY_BASE", "http://127.0.0.1:8099"))
    ap.add_argument("--node", help="hwid of the board under test (default: the first EWP/1 node online)")
    ap.add_argument("--gateway", default="192.168.0.1", help="the router's LAN address (ping target)")
    ap.add_argument("--public", default="1.1.1.1", help="a public address to ping from the board")
    ap.add_argument("--site", default="youtube.com", help="the watched site you open on the phone")
    args = ap.parse_args()
    h = Host(args.base, os.getenv("IONITY_ADMIN_TOKEN", ""))

    print(f"ESP32-MCP v3 live check against {h.base}")
    # 1 -------------------------------------------------------------------------------------
    hl = h.call("GET", "/api/v1/health")
    mq = hl.get("mqtt") or {}
    if hl.get("ok") and mq.get("connected"):
        rec("PASS", "host health", f"status={hl.get('status')} mqtt={mq.get('host')} connects={mq.get('connects')}")
    else:
        rec("FAIL", "host health", f"ok={hl.get('ok')} degraded={hl.get('degraded')} mqtt={mq}")

    # 2 -------------------------------------------------------------------------------------
    devs = h.call("GET", "/api/v1/devices").get("devices", [])
    ewp = [d for d in devs if d.get("protocol") == "ewp1"]
    node = next((d for d in ewp if d.get("device_id") == args.node), None) if args.node else \
        next((d for d in ewp if d.get("health") in ("online", "alerting")), ewp[0] if ewp else None)
    if node is None:
        rec("FAIL", "EWP/1 node", f"none found among {len(devs)} devices (is the board provisioned and online?)")
        return finish()
    hw = node["device_id"]
    tools = node.get("tools") or []
    rec("PASS" if "ping_host" in tools else "FAIL", "EWP/1 node",
        f"{hw} fw={node.get('fw')} health={node.get('health')} ping_host={'ping_host' in tools}")

    # 3 -------------------------------------------------------------------------------------
    for target in (args.gateway, args.public):
        r = h.call("POST", f"/api/v1/nodes/{hw}/action",
                   {"action": "ping_target", "params": {"target": target, "count": 4, "timeout_ms": 1000}})
        res = r.get("result") or {}
        ok = r.get("ok") is not False and r.get("reachable") is True
        rec("PASS" if ok else "FAIL", f"ping_from_node {target}",
            f"{res.get('received')}/{res.get('sent')} replies, rtt avg {res.get('rttAvgMs')} ms, "
            f"loss {r.get('loss_pct')} %, error={res.get('error') or r.get('error')}")
        time.sleep(2.1)                                    # the action is rate limited (1 per 2 s)

    # 4 + 5 -----------------------------------------------------------------------------------
    ip = (node.get("net") or {}).get("ip") or node.get("ip") or "<the board's IP>"
    if ask(f"On a phone: set Wi-Fi DNS to {ip} (or the router's DHCP DNS, if you have done that), "
           f"then open {args.site} in the browser."):
        t0 = time.time()
        flags = []
        for _ in range(20):
            flags = [f for f in h.call("GET", "/api/v1/dns/flagged?minutes=5").get("flags", [])
                     if args.site.split(".")[0] in json.dumps(f).lower()]
            if flags:
                break
            time.sleep(1.5)
        rec("PASS" if flags else "FAIL", f"{args.site} flagged",
            f"{len(flags)} flag(s) in {time.time() - t0:.0f}s, e.g. {json.dumps(flags[0])[:200] if flags else '-'}")
        if flags:
            led = input("    Is the board's red LED on now? [y/n/s] ").strip().lower()
            rec({"y": "PASS", "n": "FAIL"}.get(led, "SKIP"), "red LED (operator)", f"answer={led or '-'}")
    else:
        rec("SKIP", f"{args.site} flagged", "operator skipped")

    # 6 -------------------------------------------------------------------------------------
    if ask("Stop the MQTT broker (STOP.cmd or stop the mosquitto/amqtt process), wait 20 s, start it again."):
        t0, back = time.time(), False
        while time.time() - t0 < 120:
            d = next((x for x in h.call("GET", "/api/v1/devices").get("devices", []) if x.get("device_id") == hw), {})
            m = (h.call("GET", "/api/v1/health").get("mqtt") or {})
            if m.get("connected") and d.get("health") in ("online", "alerting"):
                back = True
                break
            time.sleep(2)
        rec("PASS" if back else "FAIL", "broker restart -> board back by itself",
            f"{'back' if back else 'not back'} after {time.time() - t0:.0f}s (board back-off is 1-60 s, +/-20 %)")
    else:
        rec("SKIP", "broker restart", "operator skipped")
    return finish()


def finish() -> int:
    path = f"live_check_{time.strftime('%Y%m%d-%H%M%S')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"when": time.strftime("%Y-%m-%d %H:%M:%S %z"), "results": RESULTS}, f, indent=2)
    failed = [r for r in RESULTS if r["status"] == "FAIL"]
    print(f"\n{len(RESULTS) - len(failed)} ok/skipped, {len(failed)} failed - report: {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        sys.exit(finish())
