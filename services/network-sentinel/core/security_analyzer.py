"""
AEDI - IONITY GLOBAL | Network sentinel | core/security_analyzer.py   Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Blocked-port probes, from the router's own firewall log - never invented.

The RouterOS firewall rules that drop Telnet/FTP/HTTP/SSH/API on the WAN must
have `log=yes` (optionally `log-prefix=...`); the collector reads /rest/log
and every new line becomes an alert. A typical line:

  DROP-WAN input: in:vlan11 out:(unknown 0), connection-state:new
  src-mac 00:11:22:33:44:55, proto TCP (SYN), 185.220.101.44:51234->102.33.102.218:23, len 60

Lines are deduplicated by RouterOS' `.id`, so polling the log repeatedly never
counts the same drop twice. Attacker "origin" is only what the log says (the
in-interface); there is no made-up geolocation.
"""
from __future__ import annotations

import re
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

_LINE = re.compile(
    r"(?:(?P<prefix>\S+)\s+)?(?P<chain>input|forward|prerouting|output):\s+in:(?P<in>\S+).*?"
    r"proto (?P<proto>\w+)(?: \([^)]*\))?,\s+"
    r"(?P<src>\d{1,3}(?:\.\d{1,3}){3})(?::(?P<sport>\d+))?->(?P<dst>\d{1,3}(?:\.\d{1,3}){3})(?::(?P<dport>\d+))?")
HIGH_PORTS = {22, 23, 8291, 8728, 8729}


def parse_firewall_line(msg: str) -> dict[str, Any] | None:
    m = _LINE.search(msg or "")
    if not m:
        return None
    return {"prefix": m["prefix"] if m["prefix"] and not m["prefix"].endswith(":") else None,
            "chain": m["chain"], "in_interface": m["in"], "proto": m["proto"].upper(),
            "src_ip": m["src"], "src_port": int(m["sport"]) if m["sport"] else None,
            "dst_ip": m["dst"], "dst_port": int(m["dport"]) if m["dport"] else None}


class SecurityAnalyzer:
    def __init__(self, config: dict[str, Any]):
        self.blocked_ports = {int(p["port"]): p for p in config.get("security", {}).get("blocked_ports", [])}
        self.alert_history: list[dict[str, Any]] = []
        self.flagged_ips: dict[str, dict[str, Any]] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()
        self.lines_seen = 0
        self.lines_unparsed = 0

    def record_security_event(self, attacker_ip: str, port: int, service: str, severity: str = "MEDIUM",
                              message: str = "", origin: str = "external", protocol: str = "TCP",
                              timestamp: str | None = None) -> dict[str, Any]:
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        alert = {
            "alert_id": f"ALT-{uuid.uuid4().hex[:8].upper()}",
            "timestamp": ts,
            "attacker_ip": attacker_ip,
            "target_port": port,
            "blocked_service": service,
            "severity": severity,
            "protocol": protocol,
            "action_taken": "DROPPED_BY_ROUTER",
            "origin": origin,
            "message": message or f"Connection attempt to blocked {service} (port {port}) dropped by the router",
        }
        self.alert_history.insert(0, alert)
        del self.alert_history[100:]
        f = self.flagged_ips.get(attacker_ip)
        if f is None:
            self.flagged_ips[attacker_ip] = {
                "ip": attacker_ip, "first_seen": ts, "last_seen": ts, "hit_count": 1,
                "threat_score": 75 if severity in ("HIGH", "CRITICAL") else 40, "flagged": True,
                "origin": origin, "targeted_services": [service]}
        else:
            f["last_seen"] = ts
            f["hit_count"] += 1
            f["threat_score"] = min(100, f["threat_score"] + 5)
            if service not in f["targeted_services"]:
                f["targeted_services"].append(service)
        return alert

    def ingest_firewall_log(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """New RouterOS log rows -> alerts for drops aimed at a blocked port. Returns the new alerts."""
        new = []
        for row in rows:
            rid = str(row.get(".id") or f"{row.get('time')}|{row.get('message')}")
            if rid in self._seen:
                continue
            self._seen[rid] = None
            if len(self._seen) > 5000:
                self._seen.popitem(last=False)
            self.lines_seen += 1
            p = parse_firewall_line(str(row.get("message", "")))
            if p is None:
                self.lines_unparsed += 1
                continue
            port = p["dst_port"]
            spec = self.blocked_ports.get(port) if port is not None else None
            if spec is None:
                continue
            service = spec.get("service", f"port {port}")
            new.append(self.record_security_event(
                attacker_ip=p["src_ip"], port=port, service=service,
                severity="HIGH" if port in HIGH_PORTS else "MEDIUM",
                protocol=p["proto"], origin=f"in:{p['in_interface']}",
                message=f"{p['proto']} {p['src_ip']}:{p['src_port'] or '?'} -> {p['dst_ip']}:{port} ({service}) "
                        f"dropped on {p['in_interface']}" + (f" [{p['prefix']}]" if p["prefix"] else ""),
                timestamp=str(row.get("time")) if row.get("time") else None))
        return new

    def get_recent_alerts(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.alert_history[:limit]

    def get_flagged_ips(self) -> list[dict[str, Any]]:
        return list(self.flagged_ips.values())

    def stats(self) -> dict[str, Any]:
        return {"log_lines_seen": self.lines_seen, "log_lines_unparsed": self.lines_unparsed,
                "alerts": len(self.alert_history), "flagged_ips": len(self.flagged_ips)}
