"""
AEDI - IONITY GLOBAL | Fleet host <-> real MQTT broker, end to end   Policy 986 AED

The broker is the lab's own (tools/packaging/broker/run_broker.py, amqtt) in a
separate process, so it can really go away and come back:

  1. broker DOWN at start-up  -> the host starts, /health says mqtt degraded
  2. broker UP                -> the bridge connects by itself (paho back-off)
  3. an EWP/1 node over MQTT  -> HELLO reaches the hub, a node action round-trips
  4. broker killed            -> degraded again, nothing crashes
  5. broker back              -> reconnects and tool calls work again

Needs amqtt (pip install amqtt==0.12.1); skipped without it. CI: job "mqtt-e2e".
"""
from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("amqtt")
paho = pytest.importorskip("paho.mqtt.client")

from app.ewp import ewp_protocol as P  # noqa: E402

from .test_netobserve_host import HW, cb, hello  # noqa: E402

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "tools" / "packaging" / "broker").is_dir())
BROKER = ROOT / "tools" / "packaging" / "broker" / "run_broker.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait(pred, timeout: float, what: str):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {what}")


class Broker:
    def __init__(self, port: int):
        self.port, self.proc = port, None

    def start(self):
        self.proc = subprocess.Popen([sys.executable, str(BROKER), f"127.0.0.1:{self.port}"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def up():
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return True
            except OSError:
                return False
        wait(up, 20, "the broker to listen")

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait(10)
            self.proc = None


class FakeNode:
    """An EWP/1 node on MQTT: retained HELLO, answers read_telemetry REQs."""

    def __init__(self, port: int):
        self.c = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2, client_id=HW)
        self.requests = 0
        self.ready = threading.Event()
        dn = f"{P.TOPIC_ROOT}/lab/{HW}/{P.CHANNEL['DOWN']}"

        def on_connect(c, *_):
            c.subscribe(dn, 1)
            c.publish(f"{P.TOPIC_ROOT}/lab/{HW}/hello", hello(), qos=1, retain=True)
            self.ready.set()

        def on_message(c, _u, msg):
            hdr, _body = cb.decode_frame(msg.payload)
            if hdr["type"] != P.MSG_REQ:
                return
            self.requests += 1
            res = cb.keyed("ResultBody", {"result": cb.keyed("TelemetryBody", {"seq": 1, "uptimeS": 42})})
            c.publish(f"{P.TOPIC_ROOT}/lab/{HW}/{P.CHANNEL['RESULT']}",
                      cb.encode_frame(P.MSG_RES, res, rid=hdr["rid"], tool=hdr["tool"]), qos=1)

        self.c.on_connect, self.c.on_message = on_connect, on_message
        self.c.connect("127.0.0.1", port, 30)
        self.c.loop_start()
        assert self.ready.wait(15), "fake node did not connect"

    def close(self):
        self.c.loop_stop()
        self.c.disconnect()


def test_bridge_survives_broker_down_up_and_flapping(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.config import settings
    port = free_port()
    for k, v in (("sqlite_path", str(tmp_path / "h.db")), ("mqtt_enabled", True), ("mqtt_host", "127.0.0.1"),
                 ("mqtt_port", port), ("dns_enabled", False), ("mdns_enabled", False), ("lan_sweep_enabled", False),
                 ("admin_token", "")):
        monkeypatch.setattr(settings, k, v)
    from app.main import app

    broker = Broker(port)
    node = None
    try:
        with TestClient(app) as c:
            # 1. no broker: the host is up and says so honestly
            h = c.get("/api/v1/health").json()
            assert h["ok"] is True and h["status"] == "degraded" and h["degraded"] == ["mqtt"]
            assert h["mqtt"]["state"] == "degraded" and h["mqtt"]["connected"] is False
            # 2. broker appears: the bridge connects on its own
            broker.start()
            wait(lambda: c.get("/api/v1/health").json()["mqtt"]["connected"], 40, "the bridge to connect")
            assert c.get("/api/v1/health").json()["status"] == "ok"
            # 3. EWP/1 over MQTT both ways
            node = FakeNode(port)
            wait(lambda: any(d.get("device_id") == HW for d in c.get("/api/v1/devices").json()["devices"]),
                 15, "the node's HELLO to reach the hub")
            r = c.post(f"/api/v1/nodes/{HW}/action", json={"action": "ping"}).json()
            assert r["ok"] is True and r["uptime_s"] == 42, r
            node.close()
            node = None
            # 4. broker dies: degraded, no crash
            broker.stop()
            wait(lambda: not c.get("/api/v1/health").json()["mqtt"]["connected"], 20, "the bridge to notice")
            r = c.post(f"/api/v1/nodes/{HW}/action", json={"action": "ping"}).json()
            assert r["ok"] is False and "broker" in r["error"].lower()
            # 5. broker back: reconnect, calls work again
            broker.start()
            wait(lambda: c.get("/api/v1/health").json()["mqtt"]["connected"], 40, "the bridge to reconnect")
            node = FakeNode(port)
            r = c.post(f"/api/v1/nodes/{HW}/action", json={"action": "ping"}).json()
            assert r["ok"] is True, r
            m = c.get("/api/v1/health").json()["mqtt"]
            assert m["connects"] >= 2 and m["disconnects"] >= 1 and m["ewp_frames"] >= 2
    finally:
        if node:
            node.close()
        broker.stop()
