"""
AEDI - IONITY GLOBAL | Network sentinel | core/probe_engine.py   Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Latency, jitter and loss to the configured targets (WAN gateways, resolvers),
measured - never invented:

  method icmp     one ICMP echo per round (core/icmp.py)
  method tcp:<p>  TCP connect time to port p (no privileges needed)
  method auto     ICMP; if this host may not send ICMP, tcp:53 (default)

A failed probe is a failure (rtt null, counted as loss). Jitter is the standard
deviation of real samples and is null until there are two of them; overall
latency is null when nothing answered.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from typing import Any, Awaitable, Callable

from core import icmp

logger = logging.getLogger("sentinel.probes")

Prober = Callable[[str, str, float], Awaitable[float | None]]


async def _tcp_rtt(ip: str, port: int, timeout: float) -> float | None:
    t0 = time.perf_counter()
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    rtt = (time.perf_counter() - t0) * 1000.0
    w.close()
    try:
        await w.wait_closed()
    except OSError:
        pass
    return rtt


class ProbeEngine:
    def __init__(self, config: dict[str, Any], prober: Prober | None = None):
        s = config.get("sentinel", {})
        self.targets: list[dict[str, Any]] = list(s.get("ping_targets") or [])
        self.default_method = str(s.get("probe_method", "auto"))
        self.timeout_s = float(s.get("probe_timeout_seconds", 1.0))
        self.max_history_samples = int(s.get("probe_history", 30))
        self._icmp_ok: bool | None = None          # learned on the first auto probe
        self._prober = prober or self._probe
        self.rtt_history: dict[str, list[float]] = {t["ip"]: [] for t in self.targets}
        self.loss_history: dict[str, list[bool]] = {t["ip"]: [] for t in self.targets}
        self.methods: dict[str, str] = {}

    async def _probe(self, ip: str, method: str, timeout: float) -> float | None:
        if method.startswith("tcp:"):
            self.methods[ip] = method
            return await _tcp_rtt(ip, int(method[4:]), timeout)
        if method in ("icmp", "auto") and self._icmp_ok is not False:
            try:
                rtt = await asyncio.to_thread(icmp.echo, ip, timeout)
                self._icmp_ok = True
                self.methods[ip] = "icmp"
                return rtt
            except icmp.IcmpNotPermitted:
                if method == "icmp":
                    raise
                self._icmp_ok = False
                logger.warning("ICMP not permitted on this host - probing with tcp:53 instead")
        self.methods[ip] = "tcp:53"
        return await _tcp_rtt(ip, 53, timeout)

    async def probe_target(self, ip: str, method: str | None = None) -> dict[str, Any]:
        try:
            rtt = await self._prober(ip, method or self.default_method, self.timeout_s)
        except icmp.IcmpNotPermitted as e:
            logger.error("%s: %s", ip, e)
            rtt = None
        ok = rtt is not None
        rh = self.rtt_history.setdefault(ip, [])
        lh = self.loss_history.setdefault(ip, [])
        if ok:
            rh.append(rtt)
            del rh[:-self.max_history_samples]
        lh.append(ok)
        del lh[:-self.max_history_samples]
        return {"ip": ip, "success": ok, "rtt_ms": round(rtt, 2) if ok else None}

    def _stats(self, ip: str) -> tuple[float | None, float | None, float | None]:
        rh, lh = self.rtt_history.get(ip, []), self.loss_history.get(ip, [])
        avg = round(sum(rh) / len(rh), 2) if rh else None
        jitter = round(statistics.stdev(rh), 2) if len(rh) >= 2 else None
        loss = round(lh.count(False) / len(lh) * 100.0, 1) if lh else None
        return avg, jitter, loss

    async def run_all_probes(self) -> dict[str, Any]:
        results = await asyncio.gather(*(self.probe_target(t["ip"], t.get("method")) for t in self.targets))
        rows = []
        for t, r in zip(self.targets, results):
            avg, jitter, loss = self._stats(t["ip"])
            rows.append({"name": t.get("name", t["ip"]), "ip": t["ip"], "method": self.methods.get(t["ip"]),
                         "rtt_ms": r["rtt_ms"], "avg_rtt_ms": avg, "jitter_ms": jitter, "packet_loss_pct": loss,
                         "status": "ONLINE" if r["success"] else "OFFLINE"})
        ok = [r["rtt_ms"] for r in rows if r["rtt_ms"] is not None]
        losses = [r["packet_loss_pct"] for r in rows if r["packet_loss_pct"] is not None]
        jitters = [r["jitter_ms"] for r in rows if r["jitter_ms"] is not None]
        return {
            "targets": rows,
            "overall_latency_ms": round(sum(ok) / len(ok), 2) if ok else None,
            "overall_jitter_ms": round(sum(jitters) / len(jitters), 2) if jitters else None,
            "overall_loss_pct": round(sum(losses) / len(losses), 1) if losses else None,
            "probed": len(rows),
            "answered": len(ok),
            "timestamp": time.time(),
        }

    def target_for(self, ip: str) -> dict[str, Any] | None:
        """Latest stats for one target (e.g. a WAN gateway), or None if it is not probed."""
        if not any(t["ip"] == ip for t in self.targets):
            return None
        avg, jitter, loss = self._stats(ip)
        return {"avg_rtt_ms": avg, "jitter_ms": jitter, "packet_loss_pct": loss}
