"""
Raspberry Pi agent (devices/pi-agent/ionity_agent.py) against a fake Pi filesystem.
    cd server && pytest -q
"""
import importlib.util
import json
import socket
import struct
import threading
from pathlib import Path

import pytest

from app.models import TelemetryIn, StatusIn

AGENT = Path(__file__).resolve().parents[2] / "devices" / "pi-agent" / "ionity_agent.py"
spec = importlib.util.spec_from_file_location("ionity_agent", AGENT)
ia = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ia)


@pytest.fixture
def fakepi(tmp_path, monkeypatch):
    def w(rel, text):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    w("proc/cpuinfo", "processor\t: 0\nModel\t\t: Raspberry Pi Zero 2 W Rev 1.0\nSerial\t\t: 00000000a1b2c3d4\n")
    w("proc/device-tree/model", "Raspberry Pi Zero 2 W Rev 1.0\x00")
    w("proc/meminfo", "MemTotal:  438000 kB\nMemFree:  100000 kB\nMemAvailable:  219000 kB\n")
    w("sys/class/thermal/thermal_zone0/temp", "48312\n")
    w("proc/net/wireless", "Inter-| sta-|   Quality        |\n face | tus | link level noise |\n"
                          " wlan0: 0000   52.  -58.  -256        0      0      0      0      0        0\n")
    w("proc/stat", "cpu  100 0 100 800 0 0 0 0 0 0\n")
    w("proc/loadavg", "0.42 0.30 0.20 1/123 4567\n")
    w("proc/uptime", "3600.55 3000.00\n")
    w("sys/class/leds/ACT/brightness", "0")
    w("sys/class/leds/ACT/trigger", "none [mmc0] timer heartbeat")
    monkeypatch.setattr(ia, "FS", tmp_path)
    monkeypatch.setattr(ia, "throttled", lambda: 0)
    monkeypatch.setattr(ia, "_cpu_prev", None)
    cfg = ia.load_config(None)
    cfg["state_file"] = str(tmp_path / "state.json")
    cfg["server"] = "127.0.0.1"
    return tmp_path, cfg


def test_identity_and_product(fakepi):
    assert ia.device_id() == "pi-a1b2c3d4"
    assert ia.product() == "Raspberry Pi Zero 2 W Rev 1.0"


def test_metrics(fakepi):
    root, _ = fakepi
    ia.cpu_pct()                                             # prime
    (root / "proc/stat").write_text("cpu  150 0 150 850 0 0 0 0 0 0\n")
    m = ia.collect_metrics()
    assert m["temp_c"] == 48.3
    assert m["rssi_dbm"] == -58.0
    assert m["free_heap_bytes"] == 219000 * 1024
    assert m["mem_used_pct"] == 50.0
    assert m["cpu_pct"] == pytest.approx(66.7, abs=0.1)
    assert m["load_1m"] == 0.42 and m["throttled"] == 0


def test_payloads_match_server_contract(fakepi):
    _, cfg = fakepi
    a = ia.Agent(cfg)
    t = TelemetryIn(**a.telemetry())
    assert t.device_id == "pi-a1b2c3d4" and t.site == "lab" and t.product.startswith("Raspberry")
    assert t.uptime_s == 3600
    StatusIn(**a.status("online"))
    assert a.topic("telemetry") == "ionity/lab/pi-a1b2c3d4/telemetry"


def test_dns_build_and_parse():
    q = ia.build_dns_query("ads.example.com", 0x1234)
    assert q[:2] == b"\x12\x34" and q.endswith(b"\x00\x01\x00\x01")
    with pytest.raises(ValueError):
        ia.build_dns_query("bad..name", 1)

    def answer(qid, rcode=0, ip=None, cname=False):
        hdr = struct.pack(">HHHHHH", qid, 0x8180 | rcode, 1, 1 + cname if ip else 0, 0, 0)
        qsec = q[12:]
        ans = b""
        if cname:
            ans += b"\xc0\x0c" + struct.pack(">HHIH", 5, 1, 60, 2) + b"\xc0\x0c"
        if ip:
            ans += b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + bytes(ip)
        return hdr + qsec + ans

    assert ia.parse_dns_answer(answer(0x1234, ip=[0, 0, 0, 0]), 0x1234) == "0.0.0.0"
    assert ia.parse_dns_answer(answer(0x1234, ip=[1, 2, 3, 4], cname=True), 0x1234) == "1.2.3.4"
    assert ia.parse_dns_answer(answer(0x1234, rcode=3), 0x1234) == "NXDOMAIN"
    assert ia.parse_dns_answer(answer(0x1234), 0x1234) == "NOANSWER"
    assert ia.parse_dns_answer(answer(0x9999, ip=[1, 1, 1, 1]), 0x1234) == "TIMEOUT"


def test_dns_probe_against_local_resolver():
    """A fake 'Gate^Flame' on 127.0.0.1 that blocks everything with 0.0.0.0."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def serve():
        data, addr = srv.recvfrom(512)
        reply = data[:2] + b"\x81\x80" + data[4:6] + b"\x00\x01\x00\x00\x00\x00" + data[12:] \
            + b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + b"\x00\x00\x00\x00"
        srv.sendto(reply, addr)
    th = threading.Thread(target=serve, daemon=True)
    th.start()
    try:
        ans, ms = ia.dns_probe("127.0.0.1", "doubleclick.net", timeout=2, port=port)
    finally:
        srv.close()
    assert ans == "0.0.0.0" and ms >= 0


def test_commands(fakepi):
    root, cfg = fakepi
    a = ia.Agent(cfg)
    assert a.handle_command({"action": "ping"})[:2] == (True, "pong")
    ok, detail, _ = a.handle_command({"action": "identify"})
    assert ok and "ACT" in detail
    assert (root / "sys/class/leds/ACT/trigger").read_text() == "mmc0"   # handed back
    ok, _, after = a.handle_command({"action": "set_meta", "site": "field", "label": "zero-01"})
    assert ok and after
    after()
    assert a.site == "field" and a.topic("cmd") == "ionity/field/pi-a1b2c3d4/cmd"
    assert json.loads(Path(cfg["state_file"]).read_text())["label"] == "zero-01"
    assert ia.Agent(cfg).site == "field"                                # survives restart
    assert a.handle_command({"action": "set_meta"})[0] is False
    assert a.handle_command({"action": "set_display", "driver": "sh1106"})[0] is False
    assert a.handle_command({"action": "dns_probe", "dns_server": "not-an-ip", "names": ["a.com"]})[0] is False
    assert a.handle_command({"action": "self_destruct"})[:2] == (False, "unknown action")
    ok, _, after = a.handle_command({"action": "reboot"})
    assert ok and callable(after)                                       # not called in tests


def test_once_cli(fakepi, capsys):
    _, cfg = fakepi
    Path(cfg["state_file"]).unlink(missing_ok=True)
    conf = Path(cfg["state_file"]).parent / "agent.conf"
    conf.write_text(f"[agent]\nserver = 127.0.0.1\nstate_file = {cfg['state_file']}\nsite = lab\n")
    assert ia.main(["--config", str(conf), "--once"]) == 0
    printed = capsys.readouterr().out
    reading = json.loads(printed[printed.index("{"):])
    assert reading["device_id"] == "pi-a1b2c3d4"
    assert reading["net"]["server"] == "127.0.0.1" and reading["net"]["resolved_by"] == "config"
    TelemetryIn(**reading)
