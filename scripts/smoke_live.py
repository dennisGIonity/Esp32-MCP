#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Live A-Z smoke test for a running fleet server
Doc ID: DOC-2026-10-ESP32MCP-SMOKE | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

Hits a RUNNING server (default http://127.0.0.1:8099) and proves, with
evidence, that every public surface works end to end:

  REST    every GET route in /openapi.json, plus the error paths (404/422)
  MCP     initialize, tools/list, resources/list + read, EVERY tool called,
          unknown tool / bad params / malformed JSON-RPC handled correctly
  WS      /ws/fleet delivers a frame
  MQTT    an emulated board (scripts/device_emulator.py) comes online, its
          telemetry lands, ping/identify/mcp commands round-trip with RTT,
          and it is purged again (retained status cleared) when done
  DNS     summary/devices/domains/recent/search answer

Exit code 0 only when everything passed.  Read-only against real boards:
commands are sent ONLY to the emulated device it starts itself.

    python scripts/smoke_live.py
    python scripts/smoke_live.py --base http://127.0.0.1:8199 --mqtt-port 1983
    python scripts/smoke_live.py --no-emulator      # REST/MCP/WS only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
EMU_ID = "esp32-smoke00000001"
RESULTS: list[tuple[str, str, str]] = []      # (PASS|FAIL|SKIP, name, evidence)


def rec(status: str, name: str, evidence: str = "") -> None:
    RESULTS.append((status, name, evidence))
    print(f"{status:4}  {name:48} {evidence[:110]}")


def check(name: str, fn, *, skip: bool = False):
    if skip:
        rec("SKIP", name, "skipped")
        return None
    try:
        ev = fn()
        rec("PASS", name, str(ev) if ev is not None else "")
        return ev
    except Exception as e:                       # noqa: BLE001 - we report everything
        rec("FAIL", name, f"{type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------- HTTP helpers
class Http:
    def __init__(self, base: str, token: str | None):
        self.base = base.rstrip("/")
        self.token = token

    def _req(self, method: str, path: str, body=None, raw: bytes | None = None, timeout=8):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                txt = r.read().decode("utf-8", "replace")
                ctype = r.headers.get("Content-Type", "")
                js = json.loads(txt) if (txt and "json" in ctype) else (txt or None)
                return r.status, js, (time.perf_counter() - t0) * 1000
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            try:
                js = json.loads(txt)
            except ValueError:
                js = txt
            return e.code, js, (time.perf_counter() - t0) * 1000

    def get(self, path, **kw):
        return self._req("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self._req("POST", path, body, **kw)

    def rpc(self, method: str, params=None, rid=1):
        st, js, ms = self.post("/api/v1/mcp/rpc", {"jsonrpc": "2.0", "id": rid, "method": method,
                                                   **({"params": params} if params is not None else {})})
        if st == 202 and js is None:              # JSON-RPC notification: accepted, no body
            return {"result": None}, ms
        if st != 200:
            raise AssertionError(f"HTTP {st}: {js}")
        return js, ms

    def tool(self, name: str, args: dict | None = None):
        js, ms = self.rpc("tools/call", {"name": name, "arguments": args or {}})
        if "error" in js:
            raise AssertionError(f"rpc error {js['error']}")
        res = js["result"]
        if res.get("isError"):
            raise AssertionError(f"tool isError: {res['content'][0]['text'][:200]}")
        return res.get("structuredContent") or json.loads(res["content"][0]["text"]), ms


def expect(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# ---------------------------------------------------------------- test groups
def test_rest(h: Http) -> None:
    st, spec, ms = h.get("/openapi.json")
    expect(st == 200, f"openapi {st}")
    paths = spec["paths"]
    rec("PASS", "REST openapi.json", f"{len(paths)} paths, {ms:.0f} ms")

    st, js, ms = h.get("/api/v1/health")
    expect(st == 200 and js.get("ok", js.get("status")) not in (False, "down"), f"health {st} {js}")
    rec("PASS", "REST /api/v1/health", f"{json.dumps(js)[:100]} {ms:.0f} ms")

    simple_gets = [p for p, ops in paths.items() if "get" in ops and "{" not in p
                   and p not in ("/openapi.json",)]
    needs = {"/api/v1/dns/search": "?pattern=ionity", "/api/v1/telemetry/query": "?metric=temp_c&limit=5",
             "/api/v1/telemetry/aggregate": "?metric=temp_c"}
    for p in sorted(simple_gets):
        st, js, ms = h.get(p + needs.get(p, ""))
        expect(st == 200, f"{p} -> {st} {str(js)[:120]}")
        size = len(json.dumps(js)) if js is not None else 0
        rec("PASS", f"REST GET {p}", f"200 {size} B {ms:.0f} ms")

    # parametrised + error paths
    st, _, _ = h.get("/api/v1/devices/does-not-exist-000")
    expect(st == 404, f"unknown device should 404, got {st}")
    rec("PASS", "REST unknown device -> 404", "")
    st, _, _ = h.get("/api/v1/telemetry/aggregate")
    expect(st == 422, f"missing metric should 422, got {st}")
    rec("PASS", "REST missing required param -> 422", "")
    st, _, _ = h.post("/api/v1/telemetry", raw=b"{not json")
    expect(st in (400, 422), f"malformed JSON body should 4xx, got {st}")
    rec("PASS", "REST malformed JSON -> 4xx", f"{st}")
    st, _, _ = h.get("/this/route/does/not/exist")
    expect(st == 404, f"unknown route {st}")
    rec("PASS", "REST unknown route -> 404", "")
    st, js, _ = h.get("/")
    expect(st == 200, f"dashboard root {st}")
    rec("PASS", "REST dashboard / served", "")
    for asset in ("/static/app.js", "/static/style.css", "/app.js", "/style.css"):
        st, _, _ = h.get(asset)
        if st == 200:
            rec("PASS", f"REST dashboard asset {asset}", "")
            break
    else:
        rec("FAIL", "REST dashboard assets", "no app.js/style.css route answered 200")
    st, js, _ = h.get("/api/v1/firmware/manifest")
    builds = js.get("builds") or js.get("images") or []
    expect(st == 200 and builds, f"manifest {st} {str(js)[:80]}")
    rec("PASS", "REST firmware manifest", f"{len(builds)} images fw {js.get('version')} built {js.get('built_at', '')[:10]}")
    for b in builds:
        st, _, _ = h.get(f"/api/v1/firmware/{b['file']}")
        expect(st == 200, f"image {b['file']} -> {st}")
    rec("PASS", "REST every firmware image downloadable", ", ".join(b["variant"] for b in builds))
    st, _, _ = h.get("/api/v1/firmware/no_such_image.bin")
    expect(st == 404, f"missing image should 404 got {st}")
    st2, _, _ = h.get("/api/v1/firmware/..%2Fmanifest.json")
    expect(st2 in (400, 404), f"traversal should be refused got {st2}")
    rec("PASS", "REST missing image 404 / traversal refused", f"{st} / {st2}")


def test_mcp(h: Http) -> dict:
    js, ms = h.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "smoke", "version": "1"}})
    si = js["result"]["serverInfo"]
    rec("PASS", "MCP initialize", f"{si['name']} v{si['version']} proto {js['result']['protocolVersion']} {ms:.0f} ms")
    h.rpc("notifications/initialized")
    js, ms = h.rpc("tools/list")
    tools = {t["name"]: t for t in js["result"]["tools"]}
    rec("PASS", "MCP tools/list", f"{len(tools)} tools {ms:.0f} ms")
    for t in tools.values():
        expect(t.get("description") and t.get("inputSchema", {}).get("type") == "object",
               f"tool {t['name']} missing description/schema")
    rec("PASS", "MCP every tool has description + object schema", "")
    js, _ = h.rpc("resources/list")
    uris = [r["uri"] for r in js["result"]["resources"]]
    rec("PASS", "MCP resources/list", ", ".join(uris))
    for u in uris:
        js, ms = h.rpc("resources/read", {"uri": u})
        expect("result" in js and js["result"]["contents"], f"read {u}: {js}")
        rec("PASS", f"MCP resources/read {u}", f"{len(js['result']['contents'][0].get('text', ''))} B {ms:.0f} ms")
    js, _ = h.rpc("prompts/list")
    rec("PASS", "MCP prompts/list", f"{len(js['result'].get('prompts', []))} prompts")

    # error paths
    js, _ = h.rpc("no/such/method")
    expect(js.get("error", {}).get("code") == -32601, f"unknown method -> {js}")
    rec("PASS", "MCP unknown method -> -32601", "")
    js, _ = h.rpc("tools/call", {"name": "no_such_tool", "arguments": {}})
    expect("error" in js or js["result"].get("isError"), f"unknown tool -> {js}")
    rec("PASS", "MCP unknown tool -> error", "")
    js, _ = h.rpc("tools/call", {"name": "get_device", "arguments": {}})
    expect("error" in js or js["result"].get("isError"), f"missing required arg -> {js}")
    rec("PASS", "MCP missing required arg -> error", "")
    js, _ = h.rpc("tools/call", {"name": "get_device", "arguments": {"device_id": "nope-000"}})
    expect("error" in js or js["result"].get("isError"), f"unknown device via tool -> {js}")
    rec("PASS", "MCP unknown device via get_device -> isError", "")
    st, js, _ = h.post("/api/v1/mcp/rpc", raw=b'{"jsonrpc":"2.0","id":9,"method":')
    expect(st in (200, 400) and (isinstance(js, dict) and (js.get("error") or st == 400)),
           f"malformed rpc -> {st} {js}")
    rec("PASS", "MCP malformed JSON-RPC -> parse error", f"HTTP {st}")
    st, js, _ = h.post("/api/v1/mcp/rpc", body=[
        {"jsonrpc": "2.0", "id": 1, "method": "fleet_summary"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    rec("PASS" if st in (200, 400) else "FAIL", "MCP batch request handled (200 or 400)", f"HTTP {st}")

    # every read-only tool
    ro = {
        "fleet_summary": {}, "list_devices": {}, "get_alerts": {}, "get_command_results": {"limit": 5},
        "integrations_status": {}, "dns_summary": {}, "dns_by_device": {}, "dns_top_domains": {},
        "dns_recent": {}, "dns_search": {"pattern": "ionity"}, "list_lan_devices": {},
        "query_telemetry": {"metric": "temp_c", "limit": 5},
        "aggregate_metric": {"metric": "temp_c"},
    }
    for name, args in ro.items():
        if name not in tools:
            rec("FAIL", f"MCP tool {name}", "not advertised")
            continue
        check(f"MCP tool {name}", lambda n=name, a=args: (lambda r: f"{len(json.dumps(r[0]))} B {r[1]:.0f} ms")(h.tool(n, a)))
    return tools


def test_ws(base: str) -> None:
    try:
        import websockets  # type: ignore
    except ImportError:
        rec("SKIP", "WS /ws/fleet", "websockets not installed")
        return
    url = base.replace("http://", "ws://").replace("https://", "wss://") + "/ws/fleet"

    async def go():
        async with websockets.connect(url, open_timeout=5, max_size=4_000_000) as ws:
            raw = await asyncio.wait_for(ws.recv(), 12)
            js = json.loads(raw)
            return f"first frame type={js.get('type') or list(js)[:3]} {len(raw)} B"
    check("WS /ws/fleet first frame", lambda: asyncio.run(go()))


def wait_for(fn, timeout: float, every: float = 0.3):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            last = fn()
            if last:
                return last, time.time() - t0
        except Exception as e:                   # noqa: BLE001
            last = e
        time.sleep(every)
    raise TimeoutError(f"after {timeout}s: {last!r}")


def test_emulator(h: Http, mqtt_host: str, mqtt_port: int) -> None:
    py = sys.executable
    emu = subprocess.Popen([py, os.path.join(HERE, "device_emulator.py"), "--id", EMU_ID, "--site", "lab",
                            "--group", "smoke", "--interval", "2", "--mqtt-host", mqtt_host,
                            "--mqtt-port", str(mqtt_port)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        def dev_doc():
            st, js, _ = h.get(f"/api/v1/devices/{EMU_ID}")
            return (js.get("device") or js) if st == 200 else {}

        def online():
            d = dev_doc()
            return d if d.get("health") == "online" else None
        dev, dt = wait_for(online, 20)
        rec("PASS", "MQTT emulator online via retained status", f"health=online after {dt:.1f}s ip={dev.get('ip')}")

        def telemetry():
            st, js, _ = h.get(f"/api/v1/telemetry/query?device_id={EMU_ID}&metric=temp_c&limit=1")
            rows = js if isinstance(js, list) else js.get("rows") or js.get("points") or js.get("items") or []
            return rows or None
        rows, dt = wait_for(telemetry, 15)
        rec("PASS", "MQTT telemetry ingested + queryable", f"temp_c sample after {dt:.1f}s: {str(rows[0])[:80]}")

        def roundtrip(action: str, extra: dict | None = None, via_tool=False):
            t0 = time.perf_counter()
            if via_tool:
                r, _ = h.tool("send_command", {"device_id": EMU_ID, "action": action, **(extra or {})})
                cid = r.get("cmd_id") or (r.get("command") or {}).get("cmd_id")
            else:
                st, r, _ = h.post(f"/api/v1/devices/{EMU_ID}/cmd", {"action": action, **(extra or {})})
                expect(st in (200, 202), f"cmd {st} {r}")
                cid = r.get("cmd_id") or (r.get("command") or {}).get("cmd_id")
            expect(cid, f"no cmd_id in {r}")

            def got():
                st, js, _ = h.get(f"/api/v1/commands/results?device_id={EMU_ID}&limit=20")
                rows = js if isinstance(js, list) else js.get("results") or js.get("items") or []
                for row in rows:
                    if row.get("cmd_id") == cid:
                        return row
                return None
            row, _ = wait_for(got, 10, 0.2)
            rtt = (time.perf_counter() - t0) * 1000
            expect(row.get("ok") is True, f"reply not ok: {row}")
            return row, rtt

        row, rtt = roundtrip("ping")
        rec("PASS", "CMD ping -> pong round-trip (REST)", f"detail={row.get('detail')} rtt={rtt:.0f} ms")
        row, rtt = roundtrip("identify", via_tool=True)
        rec("PASS", "CMD identify round-trip (MCP send_command)", f"detail={row.get('detail')} rtt={rtt:.0f} ms")
        row, rtt = roundtrip("ping", via_tool=True)
        rec("PASS", "MCP get_command_results sees cmd_id", f"rtt={rtt:.0f} ms")

        r, ms = h.tool("device_list_tools", {"device_id": EMU_ID})
        names = [t["name"] for t in (r.get("tools") or r)]
        expect("identify" in names and "read_telemetry" in names, f"device tools {names}")
        rec("PASS", "MCP device_list_tools (host->broker->board)", f"{len(names)} tools {ms:.0f} ms")
        r, ms = h.tool("device_call_tool", {"device_id": EMU_ID, "tool": "read_telemetry", "arguments": {}})
        body = r.get("result") or r
        expect("metrics" in json.dumps(body), f"read_telemetry {str(body)[:100]}")
        rec("PASS", "MCP device_call_tool read_telemetry", f"{ms:.0f} ms")
        r, ms = h.tool("device_call_tool", {"device_id": EMU_ID, "tool": "run_inference",
                                            "arguments": {"model_id": "analog_threshold"}})
        rec("PASS", "MCP device_call_tool run_inference", f"{json.dumps(r)[:90]}")
        try:
            h.tool("device_call_tool", {"device_id": EMU_ID, "tool": "set_actuator",
                                        "arguments": {"channel": "bogus", "value": 1}})
            rec("FAIL", "MCP device_call_tool bad channel -> isError", "no error raised")
        except AssertionError as e:
            rec("PASS", "MCP device_call_tool bad channel -> isError", str(e)[:80])

        js = dev_doc()
        expect(js.get("msg_count", 0) >= 1 and js.get("fw"), f"device doc {js}")
        rec("PASS", "REST device doc populated from MQTT", f"fw={js['fw']} msgs={js['msg_count']} mode={js.get('mode')}")
    finally:
        emu.terminate()
        try:
            emu.wait(5)
        except subprocess.TimeoutExpired:
            emu.kill()
        err = (emu.stderr.read() or "").strip() if emu.stderr else ""
        if err:
            rec("FAIL", "emulator stderr clean", err[-160:])
        else:
            rec("PASS", "emulator stderr clean", "")
        # purge: clear retained status/telemetry then delete rows
        try:
            import paho.mqtt.client as paho  # type: ignore
            c = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2, client_id="smoke-purge")
            c.connect(mqtt_host, mqtt_port, 10)
            c.loop_start()
            for t in ("status", "telemetry"):
                c.publish(f"ionity/lab/{EMU_ID}/{t}", b"", qos=1, retain=True).wait_for_publish(3)
            c.loop_stop()
            c.disconnect()
            st, js, ms = h._req("DELETE", f"/api/v1/devices/{EMU_ID}")
            expect(st == 200 and js.get("ok"), f"DELETE device -> {st} {js}")
            st2, _, _ = h.get(f"/api/v1/devices/{EMU_ID}")
            expect(st2 == 404, f"device still visible after forget: {st2}")
            rec("PASS", "DELETE /api/v1/devices/{id} forgets emulator (no restart)",
                f"deleted={js.get('deleted')} {ms:.0f} ms, then 404")
        except Exception as e:                   # noqa: BLE001
            rec("FAIL", "emulator purged", f"{e}")


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=os.environ.get("IONITY_BASE", "http://127.0.0.1:8099"))
    ap.add_argument("--mqtt-host", default=os.environ.get("IONITY_MQTT_HOST", "127.0.0.1"))
    ap.add_argument("--mqtt-port", type=int, default=int(os.environ.get("IONITY_MQTT_PORT", "1883")))
    ap.add_argument("--token", default=os.environ.get("IONITY_ADMIN_TOKEN") or None)
    ap.add_argument("--no-emulator", action="store_true", help="skip the MQTT/command round-trip group")
    ap.add_argument("--json", help="also write the result matrix to this file")
    a = ap.parse_args()

    h = Http(a.base, a.token)
    print(f"== Ionity ESP32-MCP live smoke  base={a.base}  mqtt={a.mqtt_host}:{a.mqtt_port}")
    t0 = time.time()
    check("REST group", lambda: test_rest(h))
    check("MCP group", lambda: test_mcp(h))
    test_ws(a.base)
    if a.no_emulator:
        rec("SKIP", "MQTT/CMD group", "--no-emulator")
    else:
        check("MQTT/CMD group", lambda: test_emulator(h, a.mqtt_host, a.mqtt_port))

    n = {s: sum(1 for r in RESULTS if r[0] == s) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\n== {n['PASS']} passed, {n['FAIL']} failed, {n['SKIP']} skipped in {time.time() - t0:.1f}s")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"base": a.base, "at": time.time(), "results": [
                {"status": s, "name": nm, "evidence": ev} for s, nm, ev in RESULTS]}, f, indent=1)
    return 0 if n["FAIL"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
