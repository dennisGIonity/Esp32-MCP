"""
AEDI - IONITY GLOBAL | Network sentinel against recorded RouterOS v7 REST answers   Policy 986 AED
    cd services/network-sentinel && python -m pytest -q
"""
import asyncio
import json
import re
from pathlib import Path

import pytest

from core.loadshedding import LoadSheddingEngine
from core.mikrotik_collector import MikroTikCollector, parse_uptime
from core.probe_engine import ProbeEngine
from core.security_analyzer import SecurityAnalyzer, parse_firewall_line
from core.speedtest_engine import SpeedtestEngine
from core.traffic_analyzer import TrafficAnalyzer

FX = Path(__file__).parent / "fixtures"
CORE = Path(__file__).resolve().parents[1] / "core"


def fx(name):
    return json.loads((FX / name).read_text())


CONFIG = {
    "site": {"id": "kd", "name": "Kelvin Drive"},
    "network": {
        "wan_interfaces": [
            {"id": "wan1", "name": "Liquid Fibre ISP 1", "interface": "vlan11", "gateway_ip": "102.33.102.217",
             "committed_bandwidth_mbps": 200, "priority": 1},
            {"id": "wan2", "name": "Liquid Fibre ISP 2", "interface": "ether9", "gateway_ip": "41.169.150.193",
             "committed_bandwidth_mbps": 200, "priority": 2}],
        "floors": [{"id": "ground_floor", "name": "Ground Floor (GF)", "vlans": [{"name": "Co-Working"}]},
                   {"id": "first_floor", "name": "First Floor (FF)"}],
    },
    "floor_interfaces": {"ground_floor": {"interfaces": ["vlan610"], "subnets": ["10.53.61.0/24"]},
                         "first_floor": {"interfaces": ["vlan400", "bridge-ff"], "subnets": ["10.53.81.0/24"]}},
    "mikrotik_connection": {"api_host": "10.53.20.1", "api_user": "sentinel_readonly", "api_password": "pw"},
    "security": {"blocked_ports": [{"port": 23, "service": "Telnet"}, {"port": 22, "service": "SSH"}]},
    "sentinel": {"ping_targets": [{"name": "GW1", "ip": "102.33.102.217"}, {"name": "DNS", "ip": "1.1.1.1"}]},
}


class Router:
    """Answers like RouterOS REST; `phase` picks the interface counters snapshot."""

    def __init__(self):
        self.phase, self.down, self.calls = 0, False, []

    async def __call__(self, path):
        self.calls.append(path)
        if self.down:
            raise OSError("connection refused")
        return {"interface": fx(f"routeros_interface_t{self.phase}.json"),
                "ip/dhcp-server/lease": fx("routeros_dhcp_lease.json"),
                "system/resource": fx("routeros_system_resource.json"),
                "log": fx("routeros_log.json")}[path]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_interface_counters_become_rates_and_floors():
    r, c = Router(), Clock()
    col = MikroTikCollector(CONFIG, fetch=r, clock=c)
    first = run(col.fetch_interface_stats())
    assert first["status"] == "LIVE" and first["simulated"] is False
    assert first["wan1"]["rx_mbps"] is None                     # one sample is not a rate
    r.phase, c.t = 1, 1010.0
    s = run(col.fetch_interface_stats())
    assert s["wan1"]["rx_mbps"] == 100.0 and s["wan1"]["tx_mbps"] == 20.0 and s["wan1"]["status"] == "ONLINE"
    assert s["wan2"]["rx_mbps"] == 10.0
    assert s["total_rx_mbps"] == 110.0
    gf, ff = s["floors"]["ground_floor"], s["floors"]["first_floor"]
    assert gf["rx_mbps"] == 50.0 and gf["devices"] == 2          # two BOUND leases in 10.53.61.0/24
    assert ff["rx_mbps"] == 0.0 and ff["devices"] == 1
    assert ff["missing_interfaces"] == ["bridge-ff"]             # mapped but not on the router: visible
    assert s["unmapped"] == ["ether1", "ether10"]                # nobody said where these belong
    assert s["router"] == {"uptime_s": 2 * 604800 + 3 * 86400 + 4 * 3600 + 5 * 60 + 6, "board": "CRS326-24G-2S+",
                           "version": "7.16.2 (stable)", "cpu_load_pct": 7}
    # a counter reset (reboot) is not a negative rate
    r.phase, c.t = 0, 1020.0
    assert run(col.fetch_interface_stats())["wan1"]["rx_mbps"] is None


def test_unconfigured_offline_and_opt_in_simulation(monkeypatch):
    for k in ("MIKROTIK_PASSWORD", "MIKROTIK_HOST", "MIKROTIK_USER"):
        monkeypatch.delenv(k, raising=False)
    cfg = {**CONFIG, "mikrotik_connection": {"api_host": "10.53.20.1", "api_user": "u", "api_password": "CHANGE_ME_in_env"}}
    s = run(MikroTikCollector(cfg, fetch=Router()).fetch_interface_stats())
    assert s["status"] == "UNCONFIGURED" and s["total_rx_mbps"] is None and s["simulated"] is False
    r = Router()
    r.down = True
    s = run(MikroTikCollector(CONFIG, fetch=r).fetch_interface_stats())
    assert s["status"] == "OFFLINE" and "refused" in s["error"] and s["wans"] == {}
    sim_cfg = {**CONFIG, "mikrotik_connection": {**CONFIG["mikrotik_connection"], "simulate_if_offline": True}}
    s = run(MikroTikCollector(sim_cfg, fetch=r).fetch_interface_stats())
    assert s["status"] == "SIMULATED" and s["simulated"] is True and s["source"] == "simulation"


def test_firewall_log_lines_become_alerts_once():
    p = parse_firewall_line(fx("routeros_log.json")[0]["message"])
    assert p == {"prefix": "DROP-WAN", "chain": "input", "in_interface": "vlan11", "proto": "TCP",
                 "src_ip": "185.220.101.44", "src_port": 51234, "dst_ip": "102.33.102.218", "dst_port": 23}
    sec = SecurityAnalyzer(CONFIG)
    rows = [r for r in fx("routeros_log.json") if "firewall" in r["topics"]]
    new = sec.ingest_firewall_log(rows)
    assert [(a["attacker_ip"], a["target_port"], a["severity"]) for a in new] == [
        ("45.33.32.156", 22, "HIGH"), ("185.220.101.44", 23, "HIGH")][::-1]
    assert new[0]["origin"] == "in:vlan11" and "[DROP-WAN]" in new[0]["message"]
    assert sec.ingest_firewall_log(rows) == []                   # same .id twice = counted once
    st = sec.stats()
    assert st["log_lines_seen"] == 4 and st["log_lines_unparsed"] == 1 and st["flagged_ips"] == 2


def test_no_invented_threats_at_start():
    sec = SecurityAnalyzer(CONFIG)
    assert sec.get_recent_alerts() == [] and sec.get_flagged_ips() == []


def test_live_telemetry_from_router_and_probes():
    r, c = Router(), Clock()
    rtts = {"102.33.102.217": [3.0, 5.0], "1.1.1.1": [None, None]}

    async def prober(ip, method, timeout):
        return rtts[ip].pop(0)
    col = MikroTikCollector(CONFIG, fetch=r, clock=c)
    ls = LoadSheddingEngine(CONFIG)
    ls.api_key = ""
    ta = TrafficAnalyzer(CONFIG, col, ProbeEngine(CONFIG, prober=prober), ls, SpeedtestEngine(CONFIG),
                         SecurityAnalyzer(CONFIG))
    run(ta.get_live_telemetry())
    r.phase, c.t = 1, 1010.0
    ta._last_log_poll = 0.0
    t = run(ta.get_live_telemetry())
    w1 = t["wan_interfaces"][0]
    assert w1["rx_mbps"] == 100.0 and w1["latency_ms"] == 4.0 and w1["packet_loss_pct"] == 0.0
    assert t["wan_interfaces"][1]["latency_ms"] is None           # WAN2's gateway is not a probe target
    assert t["stability"]["latency_avg_ms"] == 5.0 and t["stability"]["packet_loss_avg_pct"] == 50.0
    assert t["stability"]["stability_score"] is not None and t["stability"]["active_wan_id"] == "wan1"
    assert t["stability"]["router_uptime_seconds"] > 0
    assert {f["floor_id"]: f["bandwidth_share_pct"] for f in t["floors"]} == {"ground_floor": 45.5, "first_floor": 0.0}
    assert len(t["recent_threats"]) == 2
    assert t["unmapped_interfaces"] == ["ether1", "ether10"]
    assert t["loadshedding"]["available"] is False and t["loadshedding"]["current_stage"] is None
    assert t["data_sources"]["router"] == {"status": "LIVE", "simulated": False, "error": None}
    assert t["peak_throughput_capacity_mbps"] == 400.0


def test_probe_engine_reports_failures_and_real_jitter():
    vals = iter([10.0, None, 14.0])

    async def prober(ip, method, timeout):
        return next(vals)
    pe = ProbeEngine({"sentinel": {"ping_targets": [{"name": "x", "ip": "192.0.2.1"}]}}, prober=prober)
    a = run(pe.run_all_probes())
    assert a["targets"][0]["jitter_ms"] is None                   # one sample: no jitter yet
    b = run(pe.run_all_probes())
    assert b["targets"][0]["rtt_ms"] is None and b["targets"][0]["status"] == "OFFLINE"
    assert b["overall_latency_ms"] is None and b["answered"] == 0
    c = run(pe.run_all_probes())
    assert c["targets"][0]["jitter_ms"] == pytest.approx(2.83, abs=0.01)
    assert c["targets"][0]["packet_loss_pct"] == pytest.approx(33.3, abs=0.1)


def test_parse_uptime():
    assert parse_uptime("1w2d3h4m5s") == 788645
    assert parse_uptime("45s") == 45 and parse_uptime("") is None and parse_uptime("bogus") is None


def test_no_random_outside_the_labelled_simulation_module():
    """Production paths must never make numbers up; core/simulation.py is the only exception."""
    offenders = [f.name for f in CORE.glob("*.py")
                 if f.name != "simulation.py" and re.search(r"^\s*(import random|from random import)",
                                                            f.read_text(), re.M)]
    assert offenders == [], f"random used outside core/simulation.py: {offenders}"
    sim = (CORE / "simulation.py").read_text()
    assert "SIMULATED DEMO DATA" in sim and '"simulated": True' in sim
