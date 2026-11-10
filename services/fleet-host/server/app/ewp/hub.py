"""
AEDI - IONITY GLOBAL | EWP/1 node hub - ESP32 firmware 3 and Pi agent 2 on the fleet host
Doc ID: DOC-2026-10-ESP32MCP-EWPHOST | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Receives every EWP/1 frame (MQTT `ionity/ewp1/<site>/<hwid>/<ch>`, or the HTTP
fallback POST /api/v1/ewp/<hwid>/<ch>) and turns it into fleet state:

  hello  HELLO      -> node registered: fw, chip, tools, actuators, metric names, NetObserve info;
                       watchlist pushed when the node's version differs from the host's
  st     STATUS     -> online / offline (Last Will) / sleeping
  tm     TELEMETRY  -> FleetRegistry.ingest (same alerts, storage, Datadog as fw 2.x)
  ev     EVENT      -> DNS_BATCH / DNS_FLAGGED into the DNS pipeline; others into the event log
  rs     ACK/RES/ERR-> completes call()

and sends tool calls DOWN (REQ on `dn`, rid = instance << 24 | counter).
The same code serves both node types: they speak the identical contract.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections import deque
from typing import Any
from collections.abc import Callable

from app.ewp import ewp_protocol as P
from app.ewp.codec import FrameError, decode_frame, encode_frame, ip4, keyed, mac, named, num

log = logging.getLogger("ionity.ewp")

HWID_RE = re.compile(r"^(?:esp32-[0-9a-f]{12}|pi-[0-9a-f]{8,16})$")
SITE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
WATCH_RETRY_S = 60.0

# body -> nested bodies, for decoding results into JSON
_NESTED: dict[str, dict[str, str | list]] = {
    "HelloBody": {"fw": "FirmwareInfo", "chip": "ChipInfo", "limits": "LinkLimits",
                  "actuators": ["ActuatorDescriptor"], "models": ["ModelDescriptor"], "net": "NetObserveInfo"},
    "TelemetryBody": {"heap": "HeapStats", "net": "NetStats", "sensors": "SensorStats",
                      "inference": "LastInference", "dns": "DnsCounters"},
    "NetworkStatsResult": {"dns": "DnsCounters"},
    "ErrorBody": {"timing": "ExecTiming"},
}
ARGS_BODY = {
    P.TOOL_READ_TELEMETRY: "ReadTelemetryArgs", P.TOOL_RUN_INFERENCE: "RunInferenceArgs",
    P.TOOL_SET_ACTUATOR: "SetActuatorArgs", P.TOOL_SET_STATE_MODE: "SetStateModeArgs",
    P.TOOL_IDENTIFY: "IdentifyArgs", P.TOOL_SET_WATCHLIST: "SetWatchlistArgs",
    P.TOOL_GET_LOCAL_DEVICES: "GetLocalDevicesArgs", P.TOOL_QUERY_DNS_LOG: "QueryDnsLogArgs",
    P.TOOL_REBOOT: "RebootArgs",
}
RESULT_BODY = {
    P.TOOL_GET_DEVICE_INFO: "HelloBody", P.TOOL_READ_TELEMETRY: "TelemetryBody",
    P.TOOL_RUN_INFERENCE: "InferenceResult", P.TOOL_SET_ACTUATOR: "SetActuatorResult",
    P.TOOL_SET_STATE_MODE: "SetStateModeResult", P.TOOL_SET_WATCHLIST: "WatchlistResult",
    P.TOOL_GET_WATCHLIST: "GetWatchlistResult", P.TOOL_GET_NETWORK_STATS: "NetworkStatsResult",
    P.TOOL_GET_LOCAL_DEVICES: "LocalDevicesResult", P.TOOL_QUERY_DNS_LOG: "DnsLogResult",
    P.TOOL_SCAN_LAN: "ScanLanResult", P.TOOL_FLUSH_DNS_CACHE: "FlushDnsCacheResult",
    P.TOOL_REBOOT: "RebootResult",
}
MODE_NAMES = {v: k for k, v in {"ACTIVE": 0, "STANDBY": 1, "INFERENCE_ACTIVE": 2, "LOW_POWER_SLEEP": 3,
                                 "FAILSAFE": 4}.items()}


def _jsonable(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray)):
        return ip4(v) or mac(v) or bytes(v).hex()
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def client_json(c: Any) -> dict:
    c = list(c) + [None, None, None] if isinstance(c, (list, tuple)) else [None, None, None]
    return {"ip": ip4(c[0]), "mac": mac(c[1]), "hostname": c[2] or None}


def query_json(q: list, clients: list) -> dict:
    q = list(q) + [0] * 9
    cl = clients[q[1]] if isinstance(q[1], int) and 0 <= q[1] < len(clients) else {}
    return {"age_ms": q[0], **cl, "qname": q[2], "qtype": q[3], "rcode": q[4], "latency_ms": q[5],
            "count": q[6], "cached": bool(q[7] & P.DNSQ_CACHED), "flagged": bool(q[7] & P.DNSQ_FLAGGED),
            "upstream_failed": bool(q[7] & P.DNSQ_UPSTREAM_FAILED), "category_id": q[8] or None}


def decode_body(body_name: str | None, v: Any) -> Any:
    """Integer-keyed CBOR -> JSON with field names, IPs/MACs as text, tuples as objects."""
    if body_name is None:
        return _jsonable(v) if v else {}
    d = named(body_name, v)
    nested = _NESTED.get(body_name, {})
    out: dict[str, Any] = {}
    for k, x in d.items():
        sub = nested.get(k)
        if isinstance(sub, list):
            out[k] = [decode_body(sub[0], i) for i in (x or [])]
        elif isinstance(sub, str):
            out[k] = decode_body(sub, x)
        else:
            out[k] = _jsonable(x)
    if body_name in ("DnsLogResult", "DnsBatch"):
        clients = [client_json(c) for c in d.get("clients") or []]
        out["queries"] = [query_json(q, clients) for q in d.get("queries") or []]
        out["clients"] = clients
    elif body_name == "LocalDevicesResult":
        out["devices"] = [{"ip": ip4(r[0]), "mac": mac(r[1]), "hostname": r[2] or None, "age_s": r[3],
                           "queries": r[4], "sources": [n for bit, n in ((1, "arp"), (2, "dhcp"), (4, "mdns"),
                                                                          (8, "dns")) if r[5] & bit]}
                          for r in (d.get("devices") or []) if isinstance(r, list) and len(r) >= 6]
    elif body_name == "NetworkStatsResult":
        out["upstreams"] = [{"ip": ip4(u[0]), "ok": u[1], "fail": u[2], "mean_ms": u[3]}
                            for u in (d.get("upstreams") or []) if isinstance(u, list) and len(u) >= 4]
    elif body_name == "GetWatchlistResult":
        out["rules"] = [{"suffix": r[0], "category_id": r[1], "flags": r[2]} for r in d.get("rules") or []]
        out["categories"] = [{"id": c[0], "name": c[1]} for c in d.get("categories") or []]
    elif body_name == "HelloBody":
        out["tools"] = [P.TOOL_NAME.get(t[0], f"0x{t[0]:02x}") for t in d.get("tools") or []]
        out["uuid"] = bytes(d["uuid"]).hex() if isinstance(d.get("uuid"), (bytes, bytearray)) else None
        out["mode"] = MODE_NAMES.get(d.get("mode"), d.get("mode"))
        out["metrics"] = [{"id": m[0], "name": m[1], "unit": m[2]} for m in d.get("metrics") or []]
    return out


class EwpHub:
    def __init__(self, registry, pipeline, rules, settings):
        self.registry = registry
        self.pipeline = pipeline
        self.rules = rules
        self.s = settings
        self.publisher: Callable[[str, bytes, int, bool], bool] | None = None
        self.instance = random.randint(1, 255)
        self._ctr = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._acks: dict[int, dict] = {}
        self._busy: set[asyncio.Task] = set()
        self._watch_attempt: dict[str, float] = {}
        self.events: deque[dict] = deque(maxlen=500)
        self.node_listeners: list[Callable[[str, str], Any]] = []
        self.stats = {"frames": 0, "bad_frames": 0, "wrong_channel": 0, "hello": 0, "telemetry": 0,
                      "dns_batches": 0, "dns_queries": 0, "dns_flags": 0, "batch_gaps": 0, "results": 0,
                      "calls": 0, "watchlist_pushes": 0}
        self._batch_seq: dict[str, int] = {}

    # -- ingress ---------------------------------------------------------------
    def handle(self, topic: str, payload: bytes, transport: str = "mqtt") -> bool:
        parts = topic.split("/")
        if len(parts) != 5 or "/".join(parts[:2]) != P.TOPIC_ROOT:
            return False
        _, _, site, hwid, ch = parts
        return self.handle_frame(site, hwid, ch, payload, transport)

    def handle_frame(self, site: str, hwid: str, ch: str, payload: bytes, transport: str = "mqtt") -> bool:
        self.stats["frames"] += 1
        if not HWID_RE.match(hwid) or not SITE_RE.match(site) or ch not in P.CHANNEL_POLICY \
                or P.CHANNEL_POLICY[ch]["dir"] != "up":
            self.stats["bad_frames"] += 1
            return False
        try:
            hdr, body = decode_frame(payload, max_len=P.LIMITS["uplinkFrame"])
        except FrameError as e:
            self.stats["bad_frames"] += 1
            log.debug("bad EWP frame from %s on %s: %s", hwid, ch, e)
            return False
        if hdr["type"] not in P.CHANNEL_POLICY[ch]["msgTypes"]:
            self.stats["wrong_channel"] += 1                  # e.g. TELEMETRY on rs: dropped and counted
            return False
        t = hdr["type"]
        try:
            if t == P.MSG_HELLO:
                self._on_hello(site, hwid, body, transport)
            elif t == P.MSG_STATUS:
                self._on_status(site, hwid, body, transport)
            elif t == P.MSG_TELEMETRY:
                self._on_telemetry(site, hwid, hdr, body, transport)
            elif t == P.MSG_EVENT:
                self._on_event(site, hwid, hdr, body)
            elif t in (P.MSG_LOG, P.MSG_CRASH):
                self._on_log(hwid, hdr, body)
            elif t in (P.MSG_ACK, P.MSG_RES, P.MSG_ERR):
                self._on_result(hwid, hdr, body)
        except Exception:                                    # noqa: BLE001 - one bad node never stops ingest
            self.stats["bad_frames"] += 1
            log.exception("EWP %s frame from %s could not be applied", ch, hwid)
            return False
        return True

    def _node(self, site: str, hwid: str) -> dict:
        d = self.registry.devices.setdefault(hwid, {"device_id": hwid, "msg_count": 0, "alerts": set(),
                                                   "metrics": {}})
        d["site"] = site
        d.setdefault("group", "default")
        d["protocol"] = "ewp1"
        d["kind"] = "pi" if hwid.startswith("pi-") else "esp32"
        if d["kind"] == "esp32" and not d.get("mac"):
            h = hwid[6:]
            d["mac"] = "-".join(h[i:i + 2] for i in range(0, 12, 2))
        return d

    def _on_hello(self, site: str, hwid: str, body: Any, transport: str) -> None:
        self.stats["hello"] += 1
        h = decode_body("HelloBody", body)
        d = self._node(site, hwid)
        fw = h.get("fw") or {}
        chip = h.get("chip") or {}
        d.update({
            "group": h.get("group") or d.get("group") or "default",
            "label": h.get("label") or d.get("label"),
            "fw": fw.get("version") or d.get("fw"),
            "product": "ionity-pi-agent" if d["kind"] == "pi" else "ionity-esp32-mcp-node",
            "chip": P.CHIP_NAMES.get(str(chip.get("model")), "OTHER"),
            "tools": h.get("tools") or [],
            "actuators": h.get("actuators") or [],
            "metric_names": {m["id"]: m["name"] for m in h.get("metrics") or []},
            "net": h.get("net"),
            "mode": h.get("mode"),
            "uuid": h.get("uuid"),
            "boot_count": h.get("bootCount"),
            "reset_reason": P.RESET_NAMES.get(str(h.get("resetReason")), h.get("resetReason")),
            "transport": transport,
            "hello_at": time.time(),
        })
        self.registry.touch_node(hwid)
        net = h.get("net") or {}
        if "set_watchlist" in d["tools"] and net.get("watchVer") != self.rules.version:
            self._spawn(self.sync_watchlist(hwid))
        elif "set_watchlist" in d["tools"]:
            d["watch_synced"] = self.rules.version

    def _on_status(self, site: str, hwid: str, body: Any, transport: str) -> None:
        st = named("StatusBody", body)
        d = self._node(site, hwid)
        state = st.get("state")
        ip = ip4(st.get("ip"))
        if ip:
            d["ip"] = ip
        if st.get("mode") is not None:
            d["mode"] = MODE_NAMES.get(st["mode"], d.get("mode"))
        name = {P.STATE_OFFLINE: "offline", P.STATE_SLEEPING: "sleeping"}.get(state, "online")
        sleep_s = max(0, int(st["wakeAtS"] - time.time())) if state == P.STATE_SLEEPING and st.get("wakeAtS") else None
        self.registry.node_status(hwid, name, transport=transport, sleep_s=sleep_s)
        for fn in list(self.node_listeners):
            try:
                fn(hwid, name)
            except Exception:                               # noqa: BLE001
                log.debug("node listener failed", exc_info=True)

    def _on_telemetry(self, site: str, hwid: str, hdr: dict, body: Any, transport: str) -> None:
        from app.models import NetInfo, TelemetryIn
        self.stats["telemetry"] += 1
        t = decode_body("TelemetryBody", body)
        d = self._node(site, hwid)
        m: dict[str, float] = {}
        heap = t.get("heap") or {}
        for src, dst in (("internalFree", "free_heap_bytes"), ("internalMinFree", "min_free_heap_bytes"),
                         ("internalLargest", "largest_free_block_bytes"), ("psramFree", "psram_free_bytes"),
                         ("dmaFree", "dma_free_bytes")):
            if num(heap.get(src)) is not None:
                m[dst] = num(heap[src])
        if heap.get("internalFree") and heap.get("internalLargest") is not None:
            m["heap_fragmentation"] = round(1 - heap["internalLargest"] / max(1, heap["internalFree"]), 3)
        net = t.get("net") or {}
        for src, dst in (("rssiDbm", "rssi_dbm"), ("channel", "wifi_channel"), ("txFail", "tx_fail"),
                         ("reconnects", "reconnects"), ("uplinkQueued", "uplink_queued")):
            if num(net.get(src)) is not None:
                m[dst] = num(net[src])
        cpu = t.get("cpu") or []
        if cpu:
            m["cpu_pct"] = round(sum(cpu) / len(cpu), 1)
            for i, c in enumerate(cpu):
                m[f"cpu{i}_pct"] = float(c)
        sensors = t.get("sensors") or {}
        if num(sensors.get("dieTempC")) is not None:
            m["temp_c"] = round(num(sensors["dieTempC"]), 2)
        for i, mv in enumerate(sensors.get("adcMv") or []):
            m[f"adc{i}_mv"] = float(mv)
        for i, v in enumerate(t.get("actuators") or []):
            m[f"act{i}"] = float(v)
        names = d.get("metric_names") or {}
        for k, v in (t.get("ext") or {}).items():
            try:
                mid = int(k)
            except (TypeError, ValueError):
                continue
            if num(v) is not None:
                m[names.get(mid, f"ext{mid}")] = num(v)
        dns = t.get("dns") or {}
        for k, v in dns.items():
            if num(v) is not None:
                m["dns_" + re.sub(r"([A-Z])", r"_\1", k).lower()] = num(v)
        if dns:
            d["dns"] = dns
        mode = MODE_NAMES.get(t.get("mode"))
        if mode is not None:
            m["state_mode"] = float(t.get("mode"))
        age = t.get("ageMs") if hdr["flags"] & P.FLAG_BUFFERED else None
        self.registry.ingest(TelemetryIn(
            device_id=hwid, site=site, group=d.get("group") or "default", label=d.get("label"),
            fw=d.get("fw"), product=d.get("product"), uptime_s=t.get("uptimeS"), seq=t.get("seq"),
            metrics=m, net=NetInfo(ip=d.get("ip"), transport="http" if transport == "http" else "mqtt"),
            age_ms=age, mode=mode))

    def _on_event(self, site: str, hwid: str, hdr: dict, body: Any) -> None:
        ev = named("EventBody", body)
        kind = ev.get("kind")
        now = time.time()
        if kind == P.EVENT_DNS_BATCH:
            b = named("DnsBatch", ev.get("dns"))
            seq = b.get("seq")
            prev = self._batch_seq.get(hwid)
            if isinstance(seq, int) and prev is not None and seq not in (prev, prev + 1) and seq > prev:
                self.stats["batch_gaps"] += 1
            if isinstance(seq, int):
                self._batch_seq[hwid] = seq
            clients = [client_json(c) for c in b.get("clients") or []]
            recs = []
            from app.dns.pipeline import DnsRecord
            for q in b.get("queries") or []:
                if not isinstance(q, list) or len(q) < 9:
                    continue
                cl = clients[q[1]] if isinstance(q[1], int) and 0 <= q[1] < len(clients) else None
                if not cl or not cl["ip"] or not isinstance(q[2], str):
                    continue
                flags = q[7] if isinstance(q[7], int) else 0
                recs.append(DnsRecord(ts=now - (q[0] or 0) / 1000.0, client_ip=cl["ip"], mac=cl["mac"],
                                      hostname=cl["hostname"], qname=q[2], qtype=q[3], rcode=q[4],
                                      latency_ms=float(q[5]) if q[5] else None, count=max(1, q[6] or 1),
                                      cached=bool(flags & P.DNSQ_CACHED),
                                      upstream_failed=bool(flags & P.DNSQ_UPSTREAM_FAILED), node_id=hwid))
            self.stats["dns_batches"] += 1
            self.stats["dns_queries"] += len(recs)
            self.pipeline.ingest(recs)
            if b.get("dropped"):
                log.warning("node %s dropped %s DNS records (ring full)", hwid, b["dropped"])
            return
        if kind == P.EVENT_DNS_FLAGGED:
            from app.dns.pipeline import DnsRecord
            f = named("DnsFlag", ev.get("flag"))
            cl = client_json(f.get("client"))
            if cl["ip"] and isinstance(f.get("qname"), str):
                self.stats["dns_flags"] += 1
                self.pipeline.flag_now(DnsRecord(ts=now - (f.get("ageMs") or 0) / 1000.0, client_ip=cl["ip"],
                                                 mac=cl["mac"], hostname=cl["hostname"], qname=f["qname"],
                                                 qtype=f.get("qtype") or 1, node_id=hwid))
            return
        rec = {"received_at": now, "device_id": hwid, "event": P.EVENT_NAMES.get(str(kind), kind),
               "detail": _jsonable({k: v for k, v in ev.items() if k != "kind"})}
        self.events.append(rec)
        self.registry.record_event(rec)

    def _on_log(self, hwid: str, hdr: dict, body: Any) -> None:
        if hdr["type"] == P.MSG_CRASH:
            c = decode_body("CrashBody", body)
            log.error("node %s crashed on its previous boot: %s", hwid, c)
            self.events.append({"received_at": time.time(), "device_id": hwid, "event": "CRASH", "detail": c})
        else:
            lg = named("LogBody", body)
            log.log({1: logging.ERROR, 2: logging.WARNING}.get(lg.get("level"), logging.INFO),
                    "[%s] %s: %s", hwid, lg.get("tag"), lg.get("msg"))

    def _on_result(self, hwid: str, hdr: dict, body: Any) -> None:
        rid = hdr["rid"]
        if hdr["type"] == P.MSG_ACK:
            self._acks[rid] = named("AckBody", body)
            return
        self.stats["results"] += 1
        fut = self._pending.get(rid)
        if fut and not fut.done():
            fut.set_result((hdr, body))

    # -- egress: tool calls -------------------------------------------------------------------
    def _rid(self) -> int:
        self._ctr = (self._ctr + 1) & 0xFFFFFF
        return (self.instance << 24) | self._ctr

    def resolve_tool(self, tool: int | str) -> int:
        if isinstance(tool, int):
            return tool
        tid = P.TOOL_ID.get(tool)
        if tid is None:
            raise ValueError(f"unknown EWP tool '{tool}'")
        return tid

    async def call(self, hwid: str, tool: int | str, args: dict | None = None, *, raw_args: bool = False,
                   timeout_s: float | None = None, flags: int = 0) -> dict[str, Any]:
        """One REQ -> RES/ERR. `args` are field names (keyed here) unless raw_args.
        Returns {"ok", "result" | "error", "status", "elapsed_ms"} - never raises for device faults."""
        d = self.registry.devices.get(hwid)
        if not d or d.get("protocol") != "ewp1":
            return {"ok": False, "error": f"unknown EWP node '{hwid}'"}
        if not self.publisher:
            return {"ok": False, "error": "MQTT broker not connected - tool calls need MQTT"}
        tid = self.resolve_tool(tool)
        name = P.TOOL_NAME.get(tid, f"0x{tid:02x}")
        if d.get("tools") and name not in d["tools"]:
            return {"ok": False, "error": f"{hwid} does not offer '{name}' (tools: {', '.join(d['tools'])})"}
        timeout = float(timeout_s or self.s.device_rpc_timeout_s)
        body = args or {}
        if not raw_args and ARGS_BODY.get(tid):
            body = keyed(ARGS_BODY[tid], body)
        rid = self._rid()
        frame = encode_frame(P.MSG_REQ, body, rid=rid, tool=tid, flags=flags,
                             deadline_ms=min(int(timeout * 1000), 0xFFFF))
        if len(frame) > P.LIMITS["downlinkFrame"]:
            return {"ok": False, "error": f"request is {len(frame)} B, over the {P.LIMITS['downlinkFrame']} B downlink limit"}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        self.stats["calls"] += 1
        t0 = time.monotonic()
        topic = f"{P.TOPIC_ROOT}/{d.get('site', 'lab')}/{hwid}/{P.CHANNEL['DOWN']}"
        try:
            if not self.publisher(topic, frame, 1, False):
                return {"ok": False, "error": "broker refused the publish (MQTT down?)"}
            hdr, rbody = await asyncio.wait_for(fut, timeout + 1.5)
        except asyncio.TimeoutError:
            await self._cancel(d, hwid, rid)
            health = self.registry._health(d)
            return {"ok": False, "error": f"{hwid} did not answer '{name}' in {timeout:.0f}s (health={health})",
                    "status": "TIMEOUT"}
        finally:
            self._pending.pop(rid, None)
            self._acks.pop(rid, None)
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        if hdr["type"] == P.MSG_ERR:
            e = decode_body("ErrorBody", rbody)
            return {"ok": False, "status": P.ST_NAMES.get(str(hdr["status"]), hdr["status"]),
                    "error": e.get("detail") or P.ST_NAMES.get(str(hdr["status"]), "error"),
                    "retry_after_ms": e.get("retryAfterMs"), "elapsed_ms": elapsed, "tool": name}
        res = named("ResultBody", rbody)
        return {"ok": True, "tool": name, "elapsed_ms": elapsed,
                "truncated": bool(hdr["flags"] & P.FLAG_TRUNCATED),
                "timing": decode_body("ExecTiming", res.get("timing")),
                "result": decode_body(RESULT_BODY.get(tid), res.get("result"))}

    async def _cancel(self, d: dict, hwid: str, rid: int) -> None:
        if self.publisher:
            topic = f"{P.TOPIC_ROOT}/{d.get('site', 'lab')}/{hwid}/{P.CHANNEL['DOWN']}"
            self.publisher(topic, encode_frame(P.MSG_CANCEL, keyed("CancelBody", {"reason": 2}), rid=rid), 1, False)

    # -- watchlist sync ----------------------------------------------------------------------
    async def sync_watchlist(self, hwid: str, force: bool = False) -> dict[str, Any]:
        d = self.registry.devices.get(hwid) or {}
        if "set_watchlist" not in (d.get("tools") or []):
            return {"ok": False, "device_id": hwid, "error": "node has no watchlist (DNS forwarder off or old firmware)"}
        now = time.monotonic()
        if not force and now - self._watch_attempt.get(hwid, -1e9) < WATCH_RETRY_S:
            return {"ok": False, "device_id": hwid, "error": "sync already attempted in the last minute"}
        self._watch_attempt[hwid] = now
        frames = self.rules.device_frames()
        for body in frames:
            r = await self.call(hwid, P.TOOL_SET_WATCHLIST, body, raw_args=True, timeout_s=8)
            if not r["ok"]:
                log.warning("watchlist push to %s failed: %s", hwid, r.get("error"))
                return {"ok": False, "device_id": hwid, "error": r.get("error")}
        self.stats["watchlist_pushes"] += 1
        d["watch_synced"] = self.rules.version
        if isinstance(d.get("net"), dict):
            d["net"]["watchVer"] = self.rules.version
        log.info("watchlist v%08x pushed to %s (%d frame(s))", self.rules.version, hwid, len(frames))
        return {"ok": True, "device_id": hwid, "version": self.rules.version, "frames": len(frames),
                "rules": len(self.rules.device_rules())}

    async def sync_all(self) -> list[dict]:
        out = []
        for hwid, d in list(self.registry.devices.items()):
            if d.get("protocol") == "ewp1" and "set_watchlist" in (d.get("tools") or []) \
                    and self.registry._health(d) in ("online", "alerting", "stale"):
                out.append(await self.sync_watchlist(hwid, force=True))
        return out

    def _spawn(self, coro) -> None:
        try:
            t = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return
        self._busy.add(t)
        t.add_done_callback(self._busy.discard)

    async def stop(self) -> None:
        for f in self._pending.values():
            if not f.done():
                f.cancel()
        for t in list(self._busy):
            t.cancel()

    def actuator_channel(self, hwid: str, names: tuple[str, ...] = ("alert_led", "alert", "pwm0", "led")) -> int | None:
        acts = (self.registry.devices.get(hwid) or {}).get("actuators") or []
        for n in names:
            for a in acts:
                if a.get("name") == n:
                    return a.get("ch")
        return acts[0].get("ch") if acts else None
