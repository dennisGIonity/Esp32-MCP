"""
AEDI - IONITY GLOBAL | End-to-end: node pairing -> DNS ingest -> rule engine -> MCP (official SDK client)
Doc ID: DOC-2026-10-ESP32MCP-E2E | Policy 986 AED | License AED 900

A real fleet server runs in a subprocess (uvicorn, its own SQLite, its own labels file,
fleet + admin tokens ON). A simulated ESP32 node pairs over HTTP and posts EWP/1 frames
built with the Pi agent's codec - exactly the bytes a node publishes. Then the official
`mcp` Python SDK connects twice:

  * Streamable HTTP  (/mcp, session, SSE notifications)
  * stdio            (mcp_stdio_proxy.py, as Claude Desktop / Claude Code spawn it)

and must see the YouTube activity flagged, attributed to the labelled device, as valid
MCP tool results - plus a notifications/resources/updated when a new hit arrives.
    cd services/fleet-host/server && pytest -q tests/test_e2e_mcp_dns.py
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp", reason="pip install mcp (the official MCP SDK) for the end-to-end test")
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
import contextlib  # noqa: E402
import httpx  # noqa: E402

SERVER_DIR = Path(__file__).resolve().parents[1]
AGENT_DIR = next(p / "devices" / "pi-agent" for p in SERVER_DIR.parents
                 if (p / "devices" / "pi-agent" / "ewp_cbor.py").exists())
sys.path.insert(0, str(AGENT_DIR))
import ewp_cbor as cb  # noqa: E402
import ewp_protocol as P  # noqa: E402

HW = "esp32-98a316e5d18c"
TV_IP, TV_MAC = "192.168.0.42", "3c:22:fb:01:02:03"
FLEET_TOKEN, ADMIN = "e2e-fleet-token-0123456789", "e2e-admin-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    labels = tmp / "device_labels.json"
    labels.write_text(json.dumps({TV_MAC: {"label": "Kitchen TV"}}))
    port = _free_port()
    env = {**os.environ, "IONITY_MQTT_ENABLED": "false", "IONITY_DNS_ENABLED": "false",
           "IONITY_MDNS_ENABLED": "false", "IONITY_LAN_SWEEP_ENABLED": "false",
           "IONITY_SQLITE_PATH": str(tmp / "fleet.db"), "IONITY_LABELS_FILE": str(labels),
           "IONITY_REQUIRE_TOKEN": "true", "IONITY_FLEET_TOKEN": FLEET_TOKEN, "IONITY_ADMIN_TOKEN": ADMIN,
           "IONITY_DD_ENABLED": "false", "PYTHONUNBUFFERED": "1"}
    log = open(tmp / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=SERVER_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if urllib.request.urlopen(base + "/api/v1/health", timeout=1).status == 200:
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError((tmp / "server.log").read_text())
    yield {"base": base, "labels": labels, "tmp": tmp}
    proc.terminate()                                    # SIGTERM -> graceful shutdown path
    try:
        rc = proc.wait(timeout=15)
        text = (tmp / "server.log").read_text()
        # uvicorn re-raises SIGTERM after a clean lifespan shutdown (rc -15 on POSIX)
        assert rc in (0, -15) and "Fleet server shut down cleanly" in text, text[-3000:]
    finally:
        if proc.poll() is None:
            proc.kill()
        log.close()


def http(base: str, path: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None):
    req = urllib.request.Request(base + path, data, headers=headers or {}, method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def post_frame(base: str, ch: str, frame: bytes, *, signed: bool = False) -> int:
    h = {"Content-Type": "application/octet-stream"}
    if signed:
        ts = str(int(time.time()))
        h["X-Fleet-Ts"] = ts
        h["X-Fleet-Signature"] = hmac.new(FLEET_TOKEN.encode(), f"{ts}.".encode() + frame, hashlib.sha256).hexdigest()
    else:
        h["X-Fleet-Token"] = FLEET_TOKEN
    return http(base, f"/api/v1/ewp/{HW}/{ch}?site=lab", frame, h)[0]


def hello_frame() -> bytes:
    return cb.encode_frame(P.MSG_HELLO, cb.keyed("HelloBody", {
        "proto": [1, 1], "hwid": HW, "uuid": b"\x07" * 16,
        "fw": cb.keyed("FirmwareInfo", {"version": "3.1.0", "elfSha": b"\x00" * 8, "idf": "v5.5.5"}),
        "chip": cb.keyed("ChipInfo", {"model": P.CHIP_ESP32_S3, "rev": 2, "cores": 2, "flashMb": 4, "psramMb": 8}),
        "limits": cb.keyed("LinkLimits", {"maxDownlink": 4096, "maxUplink": 2048, "maxInflight": 4}),
        "tools": [[P.TOOL_READ_TELEMETRY, 1, 9], [P.TOOL_SET_WATCHLIST, 1, 8]], "models": [], "actuators": [],
        "metrics": [], "tasks": [], "mode": 0, "site": "lab", "group": "bench", "label": "lab-node-01",
        "resetReason": 1, "bootCount": 1,
        "net": cb.keyed("NetObserveInfo", {"mode": 1, "port": 53, "upstreams": [socket.inet_aton("1.1.1.1")],
                                           "caps": 1, "watchVer": 1, "watchRules": 13})}))


def batch_frame(seq: int, queries: list) -> bytes:
    return cb.encode_frame(P.MSG_EVENT, cb.keyed("EventBody", {"kind": P.EVENT_DNS_BATCH, "dns": cb.keyed("DnsBatch", {
        "seq": seq, "windowMs": 10_000,
        "clients": [[socket.inet_aton(TV_IP), bytes.fromhex(TV_MAC.replace(":", "")), "kitchen-tv"],
                    [socket.inet_aton("192.168.0.43"), b"", "laptop"]],
        "queries": queries})}), rid=seq)


@pytest.fixture(scope="module")
def paired(server):
    base = server["base"]
    # unauthenticated ingest is refused
    assert http(base, "/api/v1/nodes/register", json.dumps({"hwid": HW}).encode(),
                {"Content-Type": "application/json"})[0] == 401
    st, reg = http(base, "/api/v1/nodes/register", json.dumps({"hwid": HW, "site": "lab"}).encode(),
                   {"Content-Type": "application/json", "X-Fleet-Token": FLEET_TOKEN})
    assert st == 200 and reg["topics"]["ev"] == f"ionity/ewp1/lab/{HW}/ev"
    assert post_frame(base, "hello", hello_frame()) == 200
    assert post_frame(base, "st", cb.encode_frame(P.MSG_STATUS, cb.keyed("StatusBody", {
        "state": P.STATE_ONLINE, "ip": socket.inet_aton("192.168.0.50")}))) == 200
    assert post_frame(base, "tm", cb.encode_frame(P.MSG_TELEMETRY, cb.keyed("TelemetryBody", {
        "seq": 1, "uptimeS": 42, "mode": 0,
        "heap": cb.keyed("HeapStats", {"internalFree": 181000, "internalMinFree": 160000, "internalLargest": 90000}),
        "net": cb.keyed("NetStats", {"rssiDbm": -58, "txFail": 0, "reconnects": 0, "uplinkQueued": 0})})),
        signed=True) == 200
    q = [[1800, 0, "www.youtube.com", 1, 0, 14, 2, P.DNSQ_FLAGGED, 1],
         [1700, 0, "rr1---sn-abc.googlevideo.com", 1, 0, 22, 40, P.DNSQ_FLAGGED, 1],
         [1600, 0, "i.ytimg.com", 1, 0, 9, 5, P.DNSQ_FLAGGED, 1],
         [1000, 1, "docs.python.org", 28, 0, 30, 1, 0, 0]]
    assert post_frame(base, "ev", batch_frame(1, q), signed=True) == 200
    assert http(base, f"/api/v1/ewp/{HW}/ev?site=lab", batch_frame(9, q),
                {"X-Fleet-Token": "wrong-token-xxxxxxxxxx"})[0] == 401
    time.sleep(1.6)                                       # one writer cycle
    return server


@contextlib.asynccontextmanager
async def connect(url: str, headers: dict | None = None):
    async with httpx.AsyncClient(headers=headers or {}, timeout=httpx.Timeout(30, read=60)) as hc:
        async with streamable_http_client(url, http_client=hc) as streams:
            yield streams


def _sc(result) -> dict:
    """structuredContent of a CallToolResult, checked against its text content."""
    assert result.content and result.content[0].type == "text"
    sc = result.structuredContent
    assert isinstance(sc, dict)
    if not result.isError:
        assert json.loads(result.content[0].text) == sc
    return sc


def test_streamable_http_mcp_sees_flagged_youtube(paired):
    base = paired["base"]

    async def run():
        notes: list = []

        async def on_message(msg):
            if hasattr(msg, "root") and getattr(msg.root, "method", "") == "notifications/resources/updated":
                notes.append(str(msg.root.params.uri))

        async with connect(base + "/mcp", {"Authorization": f"Bearer {ADMIN}"}) as (r, w, _):
            async with ClientSession(r, w, message_handler=on_message) as s:
                init = await s.initialize()
                assert init.protocolVersion == "2025-11-25"
                assert init.capabilities.resources.subscribe is True
                names = {t.name for t in (await s.list_tools()).tools}
                assert {"list_devices", "get_device_telemetry", "query_dns_activity", "get_flagged_alerts",
                        "manage_dns_watchlist", "manage_device_labels", "trigger_node_action"} <= names

                res = await s.call_tool("query_dns_activity", {"domain_pattern": "*.googlevideo.com",
                                                                "device_label": "Kitchen TV", "minutes": 30})
                sc = _sc(res)
                assert res.isError is False and len(sc["queries"]) == 1
                row = sc["queries"][0]
                assert row["qname"] == "rr1---sn-abc.googlevideo.com" and row["count"] == 40
                assert row["label"] == "Kitchen TV" and row["mac"] == "3c-22-fb-01-02-03"
                assert row["flagged"] is True and row["category"] == "video_streaming:youtube"
                assert row["node_id"] == HW and row["client_ip"] == TV_IP

                res = await s.call_tool("get_flagged_alerts", {"minutes": 30, "category": "youtube"})
                sc = _sc(res)
                assert res.isError is False and len(sc["clients"]) == 1
                c = sc["clients"][0]
                assert c["who"] == "Kitchen TV" and c["hits"] == 47 and c["active"] is True
                assert set(c["domains"]) <= {"www.youtube.com", "rr1---sn-abc.googlevideo.com", "i.ytimg.com"}
                assert c["nodes"] == [HW]

                res = await s.call_tool("list_devices", {"kind": "esp32"})
                dev = _sc(res)["devices"][0]
                assert dev["device_id"] == HW and dev["rssi_dbm"] == -58 and dev["free_heap_bytes"] == 181000
                assert dev["ip"] == "192.168.0.50" and dev["mac"] == "98-a3-16-e5-d1-8c" and dev["last_seen"]

                # strict validation -> a tool error the model can fix, not a protocol error
                bad = await s.call_tool("query_dns_activity", {"limit": 5000})
                assert bad.isError is True and "limit" in bad.content[0].text
                bad = await s.call_tool("trigger_node_action", {"device_id": HW, "action": "reboot"})
                assert bad.isError is True and "confirm" in bad.content[0].text

                # resources + templates
                uris = {x.uri.unicode_string() if hasattr(x.uri, "unicode_string") else str(x.uri)
                        for x in (await s.list_resources()).resources}
                assert {"esp32://devices", "esp32://dns/recent", "esp32://dns/flagged",
                        "esp32://config/watchlist"} <= uris
                tmpl = {t.uriTemplate for t in (await s.list_resource_templates()).resourceTemplates}
                assert "esp32://devices/{device_id}/telemetry" in tmpl
                snap = json.loads((await s.read_resource(f"esp32://devices/{HW}/telemetry")).contents[0].text)
                assert snap["device_id"] == HW and "free_heap_bytes" in snap["metrics"]
                flagged = json.loads((await s.read_resource("esp32://dns/flagged")).contents[0].text)
                assert flagged["active"] == 1

                # prompts
                p = await s.get_prompt("network-activity-report", {"minutes": "30"})
                assert "get_flagged_alerts" in p.messages[0].content.text
                p = await s.get_prompt("investigate-client", {"client_ip": TV_IP})
                assert p.messages[1].content.resource.uri.unicode_string().endswith(TV_IP) \
                    if hasattr(p.messages[1].content.resource.uri, "unicode_string") else True

                # watchlist management through MCP, then a notification for a new hit
                res = await s.call_tool("manage_dns_watchlist", {"action": "add", "pattern": "netflix.com",
                                                                  "category": "video_streaming:netflix"})
                assert res.isError is False and _sc(res)["added"]["pattern"] == "netflix.com"
                await s.subscribe_resource("esp32://dns/flagged")
                await asyncio.sleep(0.5)                  # the GET event stream is open
                assert post_frame(base, "ev", batch_frame(2, [[100, 1, "www.netflix.com", 1, 0, 11, 1,
                                                               P.DNSQ_FLAGGED, 3]])) == 200
                for _ in range(50):
                    if "esp32://dns/flagged" in notes:
                        break
                    await asyncio.sleep(0.1)
                assert "esp32://dns/flagged" in notes
                res = await s.call_tool("get_flagged_alerts", {"minutes": 30, "category": "netflix"})
                assert _sc(res)["clients"][0]["who"] == "laptop"

                # labels through MCP: rename the TV, the next query is attributed to the new name
                res = await s.call_tool("manage_device_labels", {"action": "set", "key": TV_MAC,
                                                                  "label": "Lounge TV"})
                assert res.isError is False
                assert json.loads(paired["labels"].read_text())["3c-22-fb-01-02-03"]["label"] == "Lounge TV"
    asyncio.run(run())


def test_unauthorised_mcp_client_can_read_but_not_change(paired):
    async def run():
        async with connect(paired["base"] + "/mcp") as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                ok = await s.call_tool("get_flagged_alerts", {"minutes": 60})
                assert ok.isError is False
                no = await s.call_tool("manage_dns_watchlist", {"action": "add", "pattern": "x.org", "category": "x"})
                assert no.isError is True and "admin token" in no.content[0].text
    asyncio.run(run())


def test_http_transport_guards(paired):
    base = paired["base"]
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                  "clientInfo": {"name": "t", "version": "1"}}}).encode()
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    assert http(base, "/mcp", init, {**h, "Origin": "http://evil.example"})[0] == 403
    assert http(base, "/mcp", init, {**h, "MCP-Protocol-Version": "1999-01-01"})[0] == 400
    ping = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}).encode()
    assert http(base, "/mcp", ping, h)[0] == 400                                   # no session
    assert http(base, "/mcp", ping, {**h, "Mcp-Session-Id": "nope"})[0] == 404     # unknown session
    assert http(base, "/mcp", None, {"Accept": "application/json"}, method="GET")[0] == 405


def test_stdio_bridge_with_official_client(paired):
    base = paired["base"]

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=[str(SERVER_DIR / "mcp_stdio_proxy.py")],
            env={**os.environ, "IONITY_MCP_URL": base + "/mcp", "IONITY_ADMIN_TOKEN": ADMIN, "IONITY_AUTOSTART": "0"})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool("get_flagged_alerts", {"minutes": 30, "category": "youtube"})
                c = _sc(res)["clients"][0]
                assert res.isError is False and c["hits"] == 47 and c["client_ip"] == TV_IP
                res = await s.call_tool("query_dns_activity", {"client_mac": TV_MAC, "flagged_only": True})
                assert {q["qname"] for q in _sc(res)["queries"]} >= {"www.youtube.com", "i.ytimg.com"}
    asyncio.run(run())
