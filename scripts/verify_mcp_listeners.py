#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | MCP listener + data-source verification (evidence run)
Doc ID: DOC-2026-10-ESP32MCP-VERIFY | Policy 986 AED
Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd | AEDI
Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
Owner: github.com/Ionity-Global-Pty-Ltd | www.ionity.today | ai@ionity.today

Proves against the RUNNING lab, with raw evidence saved as JSON:
  L0  listening sockets       netstat: :8099 host, :1883 broker, :53 resolver, :5353 mDNS
  L1  host MCP over HTTP      :8099 /api/v1/mcp/rpc  initialize, tools/list, tools/call
  L2  host MCP over stdio     server/mcp_stdio_proxy.py handshake + a FORWARDED tool call
  L3  board MCP via broker    device_list_tools / device_call_tool (host -> MQTT -> board)
  L4  board MCP direct        http://<board>/mcp on :80, host bypassed
  A1  on-board inference      run_inference, all three on-chip models + synthetic frames
  N1  host-only network view  a DNS query to the host resolver appears in dns_search;
                              no board is in that path
  M1  where state lives       host SQLite row counts vs the board's own runtime state
Read-only on real boards: tools/list, read_telemetry, get_device_info, run_inference only.

    python scripts/verify_mcp_listeners.py [--base URL] [--dns IP] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import queue
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S %z"), "checks": []}
HIDE = ("token", "secret", "password", "psk", "api_key")
INIT = {"protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "ionity-verify", "version": "1.0"}}
MODELS = ("anomaly_zscore", "rssi_motion", "analog_threshold")


def rec(cid: str, ok, name: str, ev) -> None:
    st = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    OUT["checks"].append({"id": cid, "status": st, "name": name, "evidence": ev})
    print(f"{st:4}  {cid:4} {name:62} {json.dumps(ev, default=str)[:140]}", flush=True)


def step(cid: str, name: str, fn):
    """fn() -> (ok, evidence[, value]). Any exception is recorded as FAIL."""
    try:
        r = fn()
        rec(cid, r[0], name, r[1])
        return r[2] if len(r) > 2 else r[1]
    except Exception as e:  # noqa: BLE001 - an evidence run reports everything
        rec(cid, False, name, f"{type(e).__name__}: {e}")
        return None


def scrub(d):
    if isinstance(d, dict):
        return {k: ("<hidden>" if any(h in str(k).lower() for h in HIDE) else scrub(v)) for k, v in d.items()}
    return [scrub(x) for x in d] if isinstance(d, list) else d


def http(url: str, body=None, timeout: float = 10.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        txt = r.read().decode("utf-8", "replace")
    ms = round((time.perf_counter() - t0) * 1000, 1)
    try:
        return (json.loads(txt) if txt else None), ms
    except ValueError:
        return txt, ms


_n = [0]


def rpc(url: str, method: str, params=None, timeout: float = 15.0):
    _n[0] += 1
    return http(url, {"jsonrpc": "2.0", "id": _n[0], "method": method, "params": params or {}}, timeout)


def payload(resp):
    """tools/call response -> (parsed result, is_error)."""
    if not isinstance(resp, dict):
        return resp, True
    if "error" in resp:
        return resp["error"], True
    res = resp.get("result") or {}
    err = bool(res.get("isError"))
    if "structuredContent" in res:
        return res["structuredContent"], err
    for c in res.get("content", []):
        if c.get("type") == "text":
            try:
                return json.loads(c["text"]), err
            except ValueError:
                return c["text"], err
    return res, err


def find_tools(x):
    if isinstance(x, dict):
        if isinstance(x.get("tools"), list):
            return [t.get("name") for t in x["tools"] if isinstance(t, dict)]
        for v in x.values():
            n = find_tools(v)
            if n:
                return n
    return []


# ------------------------------------------------------------------ L0 sockets
def listeners(lan_ip=None):
    rows = {}
    for line in subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout.splitlines():
        p = line.split()
        if len(p) < 4 or p[0] not in ("TCP", "UDP") or p[-1] == "0":
            continue
        port = p[1].rsplit(":", 1)[-1]
        # A TCP listener row has foreign address *:0. Its displayed STATE is unreliable for
        # some Python sockets on Windows (seen as FIN_WAIT_1 or blank), so key on the
        # foreign address, never the state column; PID 0 rows are phantoms / TIME_WAIT.
        if port in ("8099", "1883", "53", "5353") and (p[0] == "UDP" or p[2].endswith(":0")):
            rows[f"{p[0]} {p[1]}"] = p[-1]
    pids = sorted(set(rows.values()))
    cmd = {}
    if pids:
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -in @(" + ",".join(pids) +
              ") } | ForEach-Object { '{0}|{1} {2}' -f $_.ProcessId, $_.Name, $_.CommandLine }")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
        cmd = dict(ln.split("|", 1) for ln in out.splitlines() if "|" in ln)
    view = {k: {"pid": v, "process": cmd.get(v, "?").strip()} for k, v in sorted(rows.items())}
    probes = {}
    for host in dict.fromkeys(filter(None, ("127.0.0.1", lan_ip))):
        for port in (8099, 1883):
            t0 = time.perf_counter()
            try:
                with socket.create_connection((host, port), timeout=2):
                    probes[f"{host}:{port}"] = f"connect OK {round((time.perf_counter() - t0) * 1000, 1)} ms"
            except OSError as e:
                probes[f"{host}:{port}"] = f"FAIL {type(e).__name__}: {e}"
    ok = all(v.startswith("connect OK") for v in probes.values()) and \
        any(k.startswith("UDP") and k.endswith(":53") for k in rows)
    return ok, {"tcp_connect_probes": probes, "netstat_listeners": view}


# --------------------------------------------------------------- L1 MCP / HTTP
def host_http(mcp: str):
    r, ms = rpc(mcp, "initialize", INIT)
    res = r.get("result", {})
    rec("L1a", "serverInfo" in res, "host MCP/HTTP :8099 initialize",
        {"serverInfo": res.get("serverInfo"), "protocol": res.get("protocolVersion"), "ms": ms})
    r, ms = rpc(mcp, "tools/list")
    names = [t["name"] for t in r["result"]["tools"]]
    rec("L1b", len(names) >= 18, "host MCP/HTTP tools/list", {"count": len(names), "tools": names, "ms": ms})
    r, ms = rpc(mcp, "tools/call", {"name": "fleet_summary", "arguments": {}})
    s, err = payload(r)
    return not err, {"result": s, "ms": ms}, s


# -------------------------------------------------------------- L2 MCP / stdio
def host_stdio(http_summary):
    env = dict(os.environ, IONITY_AUTOSTART="0")
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "server", "mcp_stdio_proxy.py")],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, env=env, cwd=ROOT)
    q: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [q.put(x) for x in p.stdout], daemon=True).start()

    def ask(obj, timeout=30.0):
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()
        if "id" not in obj:
            return None
        end = time.time() + timeout
        while time.time() < end:
            try:
                m = json.loads(q.get(timeout=max(0.05, end - time.time())))
            except (queue.Empty, ValueError):
                continue
            if isinstance(m, dict) and m.get("id") == obj["id"]:
                return m
        raise TimeoutError(f"stdio bridge gave no reply to id {obj['id']}")

    try:
        a = ask({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INIT})
        ask({"jsonrpc": "2.0", "method": "notifications/initialized"})
        b = ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        t0 = time.perf_counter()
        c = ask({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "fleet_summary", "arguments": {}}})
        ms = round((time.perf_counter() - t0) * 1000, 1)
    finally:
        try:
            p.stdin.close()
            p.wait(timeout=10)
        except Exception:  # noqa: BLE001
            p.kill()
    rec("L2a", "result" in a, "host MCP/stdio initialize (bridge handshake)",
        {"serverInfo": a["result"].get("serverInfo"), "protocol": a["result"].get("protocolVersion")})
    rec("L2b", len(b["result"]["tools"]) >= 18, "host MCP/stdio tools/list", {"count": len(b["result"]["tools"])})
    s, err = payload(c)
    same = isinstance(s, dict) and isinstance(http_summary, dict) and \
        s.get("total_devices") == http_summary.get("total_devices")
    return (not err and same), {"result": s, "same_as_http": same, "ms": ms}


# ------------------------------------------------- fleet registry + real boards
KEEP = ("device_id", "label", "health", "ip", "fw", "fw_version", "chip", "transport",
        "mode", "mcp_url", "site", "group", "last_seen")


def registry(mcp: str):
    r, ms = rpc(mcp, "tools/call", {"name": "list_devices", "arguments": {"limit": 100}})
    d, err = payload(r)
    devs = d.get("devices", d.get("items", [])) if isinstance(d, dict) else (d or [])
    view = [{k: x.get(k) for k in KEEP if k in x} for x in devs]
    online = [x for x in devs if x.get("health") == "online" and x.get("ip")]
    return not err, {"devices": view, "online": [x["device_id"] for x in online], "ms": ms}, online


def board_via_host(mcp: str, did: str):
    r, ms = rpc(mcp, "tools/call", {"name": "device_list_tools", "arguments": {"device_id": did}}, 30)
    d, err = payload(r)
    names = find_tools(d)
    rec("L3a", not err and bool(names), f"{did}: device_list_tools (host->MQTT->board)",
        {"tools": names, "ms": ms})
    r, ms = rpc(mcp, "tools/call", {"name": "device_call_tool", "arguments":
                {"device_id": did, "tool": "read_telemetry", "arguments": {}}}, 30)
    d, err = payload(r)
    return not err, {"result": scrub(d), "ms": ms}


def board_direct(dev: dict):
    did, ip = dev["device_id"], dev["ip"]
    url = f"http://{ip}/mcp"
    t0 = time.perf_counter()
    with socket.create_connection((ip, 80), timeout=4):
        pass
    rec("L4a", True, f"{did}: TCP {ip}:80 accepts (board's own listener)",
        {"connect_ms": round((time.perf_counter() - t0) * 1000, 1)})
    info, ms = http(f"http://{ip}/info", timeout=8)
    rec("L4b", isinstance(info, dict), f"{did}: GET http://{ip}/info (direct)", {"info": scrub(info), "ms": ms})
    r, ms = rpc(url, "initialize", INIT, 10)
    rec("L4c", "result" in (r or {}), f"{did}: POST {url} initialize (host bypassed)",
        {"result": (r or {}).get("result"), "ms": ms})
    r, ms = rpc(url, "tools/list", None, 10)
    rec("L4d", bool(find_tools(r)), f"{did}: POST {url} tools/list (direct)", {"tools": find_tools(r), "ms": ms})
    for cid, tool in (("L4e", "read_telemetry"), ("L4f", "get_device_info")):
        r, ms = rpc(url, "tools/call", {"name": tool, "arguments": {}}, 10)
        d, err = payload(r)
        rec(cid, not err, f"{did}: {tool} (direct)", {"result": scrub(d), "ms": ms})
    for i, m in enumerate(MODELS):
        r, ms = rpc(url, "tools/call", {"name": "run_inference", "arguments": {"model_id": m}}, 10)
        d, err = payload(r)
        rec("A1" + "abc"[i], not err, f"{did}: on-chip run_inference {m} (live data)", {"result": d, "ms": ms})
    base, labels = [1.00, 1.02, 0.98, 1.01, 0.99] * 6, {}
    for cid, tail in (("A1d", 1.0), ("A1e", 3.0)):
        r, ms = rpc(url, "tools/call", {"name": "run_inference", "arguments":
                    {"model_id": "anomaly_zscore", "input_frame": base + [tail]}}, 10)
        d, err = payload(r)
        labels[str(tail)] = d.get("label") if isinstance(d, dict) else None
        rec(cid, not err and labels[str(tail)] is not None,
            f"{did}: anomaly_zscore on a synthetic frame ending {tail}", {"result": d, "ms": ms})
    return labels["1.0"] != labels["3.0"], {"labels_by_last_value": labels}


# ----------------------------------------------------- N1 host-only network view
def dns_query(server: str, name: str, qtype: int = 1, timeout: float = 4.0):
    tid = int(time.time() * 1000) & 0xFFFF
    q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    q += struct.pack(">HH", qtype, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    t0 = time.perf_counter()
    try:
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(4096)
    finally:
        s.close()
    flags, ancount = struct.unpack(">H", data[2:4])[0], struct.unpack(">H", data[6:8])[0]
    return {"rcode": flags & 0xF, "answers": ancount, "ms": round((time.perf_counter() - t0) * 1000, 1)}


def network_view(mcp: str, dns_ip: str):
    marker = f"verify-{int(time.time())}.ionity.today"
    sent = {}
    for n in (marker, "www.ionity.today"):
        try:
            sent[n] = dns_query(dns_ip, n)
        except OSError as e:
            sent[n] = f"{type(e).__name__}: {e}"
    rec("N1a", all(isinstance(v, dict) for v in sent.values()),
        f"DNS sent to the HOST resolver {dns_ip}:53 (no board in the path)", sent)
    time.sleep(3.5)                          # the resolver flushes its log every 2 s
    r, ms = rpc(mcp, "tools/call", {"name": "dns_search", "arguments": {"pattern": marker.split(".")[0]}})
    d, err = payload(r)
    rec("N1b", (not err) and marker in json.dumps(d), "MCP dns_search returns that exact query",
        {"pattern": marker.split(".")[0], "result": d, "ms": ms})
    r, _ = rpc(mcp, "tools/call", {"name": "dns_summary", "arguments": {}})
    d, err = payload(r)
    rec("N1c", not err, "MCP dns_summary (host resolver is the data source)", {"result": d})
    r, _ = rpc(mcp, "tools/call", {"name": "list_lan_devices", "arguments": {}})
    d, err = payload(r)
    return not err, {"result": d}


# --------------------------------------------------------- M1 where state lives
def host_state():
    db = os.path.join(ROOT, "data", "fleet.db")
    con = sqlite3.connect(pathlib.Path(db).as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        tabs = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tabs}
    finally:
        con.close()
    return bool(counts), {"db": db, "size_mb": round(os.path.getsize(db) / 1e6, 2), "rows": counts}


def health(base: str):
    h, _ = http(base + "/api/v1/health")
    return bool(isinstance(h, dict) and h.get("ok")), scrub(h)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8099")
    ap.add_argument("--dns", default=None, help="host resolver IP (default: from /api/v1/health)")
    ap.add_argument("--out", default=os.path.join(ROOT, "logs", "verify-mcp-listeners.json"))
    a = ap.parse_args()
    base = a.base.rstrip("/")
    mcp = base + "/api/v1/mcp/rpc"
    try:
        rev = subprocess.run(["git", "-c", "safe.directory=*", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        rev = "?"
    OUT["env"] = {"host": socket.gethostname(), "python": sys.version.split()[0], "git": rev, "base": base}
    print(f"== Ionity ESP32-MCP listener verification  base={base}  git={rev}")
    h = step("H0", "host health", lambda: health(base)) or {}
    adv = (h.get("discovery") or {}).get("advertised_ip")
    step("L0", "listeners: real TCP connects + netstat owner", lambda: listeners(adv))
    summ = step("L1c", "host MCP/HTTP tools/call fleet_summary", lambda: host_http(mcp))
    step("L2c", "host MCP/stdio tools/call forwarded to :8099", lambda: host_stdio(summ))
    online = step("F1", "fleet registry held by the HOST (list_devices)", lambda: registry(mcp)) or []
    if not online:
        rec("L3", None, "real board via host", "no real board online")
        rec("L4", None, "real board direct", "no real board online")
    for dev in online:
        did = dev["device_id"]
        step("L3b", f"{did}: device_call_tool read_telemetry (via host)", lambda d=did: board_via_host(mcp, d))
        step("A1f", f"{did}: direct MCP done; z-score separates spike vs no-spike",
             lambda d=dev: board_direct(d))
    dns = h.get("dns") if isinstance(h.get("dns"), dict) else {}
    bind = str(dns.get("bind") or "")
    bind = bind.rsplit(":", 1)[0] if bind.count(":") == 1 else bind
    dns_ip = a.dns or (bind if bind not in ("", "0.0.0.0", "::") else None) \
        or (h.get("discovery") or {}).get("advertised_ip") or "127.0.0.1"
    step("N1d", "MCP list_lan_devices (host ARP view, no board)", lambda: network_view(mcp, dns_ip))
    step("M1", "host 'mainframe' state: SQLite row counts (read-only)", host_state)
    st = [c["status"] for c in OUT["checks"]]
    OUT.update(finished=time.strftime("%Y-%m-%d %H:%M:%S %z"), passed=st.count("PASS"),
               failed=st.count("FAIL"), skipped=st.count("SKIP"))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(OUT, f, indent=2, default=str)
    print(f"\n== {OUT['passed']} passed, {OUT['failed']} failed, {OUT['skipped']} skipped  evidence -> {a.out}")
    sys.exit(1 if OUT["failed"] else 0)


if __name__ == "__main__":
    main()
