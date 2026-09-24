import time
import random
import uuid
from typing import Dict, List, Any
from datetime import datetime, timezone

class SecurityAnalyzer:
    """
    Security analyzer tracking probes on blocked ports (Telnet, FTP, WWW, SSH, API)
    and flagging suspicious IP addresses.
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.blocked_ports = config.get("security", {}).get("blocked_ports", [
            {"port": 23, "service": "Telnet"},
            {"port": 21, "service": "FTP"},
            {"port": 80, "service": "WWW (HTTP)"},
            {"port": 22, "service": "SSH"},
            {"port": 8728, "service": "MikroTik API"}
        ])

        self.alert_history: List[Dict[str, Any]] = []
        self.flagged_ips: Dict[str, Dict[str, Any]] = {}
        self._init_mock_threats()

    def _init_mock_threats(self):
        sample_threats = [
            {
                "ip": "185.220.101.44",
                "service": "Telnet",
                "port": 23,
                "severity": "HIGH",
                "origin": "Tor Exit Relay",
                "msg": "SYN flood / Telnet brute force attempt on WAN1"
            },
            {
                "ip": "45.33.32.156",
                "service": "SSH",
                "port": 22,
                "severity": "HIGH",
                "origin": "Known Scanner (Linode ASN)",
                "msg": "Repeated SSH key exchange probes blocked by firewall"
            },
            {
                "ip": "194.26.29.112",
                "service": "MikroTik API",
                "port": 8728,
                "severity": "CRITICAL",
                "origin": "Rogue Autonomous System",
                "msg": "RouterOS WinBox/API exploit attempt (CVE-2018-14847 vector)"
            },
            {
                "ip": "89.248.163.201",
                "service": "FTP",
                "port": 21,
                "severity": "MEDIUM",
                "origin": "Recurrent Botnet Scanner",
                "msg": "Anonymous FTP login sequence dropped"
            }
        ]

        for t in sample_threats:
            self.record_security_event(
                attacker_ip=t["ip"],
                port=t["port"],
                service=t["service"],
                severity=t["severity"],
                message=t["msg"],
                origin=t["origin"]
            )

    def record_security_event(self, attacker_ip: str, port: int, service: str, severity: str = "MEDIUM", message: str = "", origin: str = "External") -> Dict[str, Any]:
        alert = {
            "alert_id": f"ALT-{uuid.uuid4().hex[:8].upper()}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attacker_ip": attacker_ip,
            "target_port": port,
            "blocked_service": service,
            "severity": severity,
            "protocol": "TCP",
            "action_taken": "DROP_AND_FLAG",
            "origin": origin,
            "message": message or f"Connection attempt to blocked {service} service (Port {port}) dropped by CRS326 firewall"
        }

        self.alert_history.insert(0, alert)
        if len(self.alert_history) > 100:
            self.alert_history.pop()

        if attacker_ip not in self.flagged_ips:
            self.flagged_ips[attacker_ip] = {
                "ip": attacker_ip,
                "first_seen": alert["timestamp"],
                "last_seen": alert["timestamp"],
                "hit_count": 1,
                "threat_score": 75 if severity in ["HIGH", "CRITICAL"] else 40,
                "flagged": True,
                "origin": origin,
                "targeted_services": [service]
            }
        else:
            self.flagged_ips[attacker_ip]["last_seen"] = alert["timestamp"]
            self.flagged_ips[attacker_ip]["hit_count"] += 1
            if service not in self.flagged_ips[attacker_ip]["targeted_services"]:
                self.flagged_ips[attacker_ip]["targeted_services"].append(service)

        return alert

    def get_recent_alerts(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self.alert_history[:limit]

    def get_flagged_ips(self) -> List[Dict[str, Any]]:
        return list(self.flagged_ips.values())

    def simulate_random_threat(self) -> Optional[Dict[str, Any]]:
        """Occasionally generates a new probe attempt for real-time dashboard updates."""
        if random.random() < 0.15: # 15% chance per cycle
            target = random.choice(self.blocked_ports)
            rand_ip = f"{random.randint(45, 195)}.{random.randint(10, 250)}.{random.randint(1, 254)}.{random.randint(1, 254)}"
            return self.record_security_event(
                attacker_ip=rand_ip,
                port=target["port"],
                service=target["service"],
                severity="HIGH" if target["port"] in [22, 8728] else "MEDIUM",
                message=f"Unauthorized probe on {target['service']} (port {target['port']}) intercepted and blocked."
            )
        return None
