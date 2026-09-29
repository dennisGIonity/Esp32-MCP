#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | fw 2.0 board emulator (on-device MCP over MQTT)
Doc ID: DOC-2026-09-ESP32MCP-EMU | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

One process = one Ionity ESP32 node running firmware 2.0, as the broker sees
it: telemetry every 10 s, retained status + Last Will, and the "mcp" command
answered with the same JSON-RPC the real DeviceMcp.ino produces (tools/list,
tools/call for get_device_info, read_telemetry, run_inference, set_actuator,
set_state_mode, identify). Use it to prove the host -> broker -> board MCP
path, the dashboard and Datadog before hardware is on the bench.

    python scripts/device_emulator.py                        # esp32-emu000000001 on 127.0.0.1:1883
    python scripts/device_emulator.py --id esp32-emu0002 --site lab --interval 5
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import threading
import time

import paho.mqtt.client as paho

FW = "2.0.0"
MODES = ["ACTIVE", "STANDBY", "INFERENCE_ACTIVE", "LOW_POWER_SLEEP", "FAILSAFE"]
CHANNELS = ["led", "alert_led", "pwm0", "pwm1", "relay0"]
MODELS = ["anomaly_zscore", "rssi_motion", "analog_threshold"]

TOOLS = [
    {"name": "get_device_info", "title": "Device info", "inputSchema": {"type": "object", "properties": {}},
     "description": "Identity, chip, firmware, provisioning (no secrets), network, state mode and tools."},
    {"name": "read_telemetry", "title": "Read telemetry", "inputSchema": {"type": "object", "properties": {}},
     "description": "Live readings: temperature, ADC, RSSI, heap, loop timing, actuators, last inference."},
    {"name": "run_inference", "title": "Run edge inference", "description": "On-chip model -> label + confidence.",
     "inputSchema": {"type": "object", "required": ["model_id"], "properties": {
         "model_id": {"type": "string", "enum": MODELS},
         "input_frame": {"type": "array", "items": {"type": "number"}, "maxItems": 64},
         "threshold": {"type": "number"}}}},
    {"name": "set_actuator", "title": "Set actuator", "description": "led/alert_led/relay0 0|1, pwm0/pwm1 duty 0..1.",
     "inputSchema": {"type": "object", "required": ["channel", "value"], "properties": {
         "channel": {"type": "string", "enum": CHANNELS}, "value": {"type": "number", "minimum": 0, "maximum": 1}}}},
    {"name": "set_state_mode", "title": "Set state mode", "description": "STANDBY|ACTIVE|INFERENCE_ACTIVE|LOW_POWER_SLEEP|FAILSAFE",
     "inputSchema": {"type": "object", "required": ["mode"], "properties": {
         "mode": {"type": "string", "enum": [m for m in MODES]},
         "duration_s": {"type": "integer"}, "model_id": {"type": "string", "enum": MODELS}}}},
    {"name": "identify", "title": "Identify board", "description": "Blink the LED.",
     "inputSchema": {"type": "object", "properties": {}}},
]


class Board:
    def __init__(self, a):
        self.a = a
        self.id, self.site, self.group = a.id, a.site, a.group
        self.mode = "ACTIVE"
        self.act = {c: None for c in CHANNELS}
        self.analog: list[float] = []
        self.rssi: list[float] = []
        self.t0 = time.time()
        self.tx = 0
        self.last_inf = None
        self.base = f"ionity/{self.site}/{self.id}/"
        self.c = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2, client_id=self.id)
        self.c.will_set(self.base + "status", json.dumps(self.status("offline")), qos=1, retain=True)
        self.c.on_connect = self.on_connect
        self.c.on_message = self.on_message
        self.lock = threading.Lock()

    def status(self, state: str) -> dict:
        return {"device_id": self.id, "site": self.site, "group": self.group, "label": self.a.label,
                "fw": FW, "state": state, "ip": "10.0.0.42", "uptime_s": int(time.time() - self.t0),
                "transport": "mqtt", "mode": self.mode, "mcp": True}

    def on_connect(self, c, *_):
        c.subscribe([(self.base + "cmd", 1), ("ionity/broadcast/cmd", 1)])
        c.publish(self.base + "status", json.dumps(self.status("online")), qos=1, retain=True)
        print(f"[emu] {self.id} online at {self.a.mqtt_host}:{self.a.mqtt_port}")

    # -- sensors ------------------------------------------------------------
    def sample(self):
        t = time.time()
        v = 1.2 + 0.05 * math.sin(t / 7) + random.gauss(0, 0.01)
        if random.random() < 0.02:
            v += 0.6                                   # an occasional spike for anomaly_zscore
        r = -58 + random.gauss(0, 0.8 if random.random() > 0.3 else 3.5)
        with self.lock:
            self.analog = (self.analog + [v])[-64:]
            self.rssi = (self.rssi + [r])[-64:]

    def infer(self, model, frame=None, thr=None):
        x = frame or (self.analog if model != "rssi_motion" else self.rssi)
        if model == "anomaly_zscore":
            if len(x) < 8:
                return None, "need >= 8 samples"
            base = x[:-1]
            sd = statistics.pstdev(base) or 1e-6
            z = (x[-1] - statistics.mean(base)) / sd
            lim = thr or 3.0
            lab = "anomaly" if abs(z) > lim else "normal"
            conf = min(1, abs(z) / (lim * 1.333))
            return {"label": lab, "score": z, "confidence": conf if lab == "anomaly" else 1 - conf}, None
        if model == "rssi_motion":
            if len(x) < 8:
                return None, "need >= 8 RSSI samples"
            sd = statistics.stdev(x)
            lim = thr or 2.5
            lab = "motion" if sd > lim else "still"
            c = min(1, sd / (lim * 2))
            return {"label": lab, "score": sd, "confidence": max(0.5, c) if lab == "motion" else 1 - c}, None
        if model == "analog_threshold":
            lim = thr or 1.65
            return {"label": "above" if x[-1] > lim else "below", "score": x[-1],
                    "confidence": min(1, 0.5 + abs(x[-1] - lim) / 3.3)}, None
        return None, "unknown model_id"

    # -- MCP ------------------------------------------------------------------
    def telemetry_doc(self) -> dict:
        return {"device_id": self.id, "fw": FW, "uptime_s": int(time.time() - self.t0), "mode": self.mode,
                "metrics": {"temp_c": round(41 + random.gauss(0, .3), 1), "analog_v": round(self.analog[-1], 3) if self.analog else 0,
                            "rssi_dbm": round(self.rssi[-1]) if self.rssi else -60,
                            "free_heap_bytes": 212000 + random.randint(-800, 800), "min_free_heap_bytes": 198400,
                            "max_alloc_heap_bytes": 110580, "loop_us_avg": 170 + random.randint(0, 40),
                            "loop_us_max": 2400 + random.randint(0, 900), "freertos_tasks": 17},
                "actuators": {c: {"value": v} for c, v in self.act.items()},
                "last_inference": self.last_inf, "emulated": True}

    def call_tool(self, name, args):
        if name == "get_device_info":
            return {"device_id": self.id, "fw": FW, "chip": "ESP32-S3 (emulated)", "mode": self.mode,
                    "tools": [t["name"] for t in TOOLS], "role": "node", "emulated": True}, False
        if name == "read_telemetry":
            return self.telemetry_doc(), False
        if name == "run_inference":
            r, err = self.infer(args.get("model_id"), args.get("input_frame"), args.get("threshold"))
            if err:
                return {"error": err}, True
            self.last_inf = {"model": args["model_id"], **r}
            return {"model_id": args["model_id"], **r, "source": "input_frame" if args.get("input_frame") else "live"}, False
        if name == "set_actuator":
            ch, v = args.get("channel"), args.get("value")
            if ch not in CHANNELS:
                return {"error": "channel must be led|alert_led|pwm0|pwm1|relay0"}, True
            if self.mode == "FAILSAFE" and v:
                return {"error": "FAILSAFE: actuators locked off (set_state_mode ACTIVE first)"}, True
            if not isinstance(v, (int, float)) or not 0 <= v <= 1:
                return {"error": "value must be 0..1"}, True
            self.act[ch] = v if ch.startswith("pwm") else (1 if v >= .5 else 0)
            return {"channel": ch, "value": self.act[ch], "mode": self.mode}, False
        if name == "set_state_mode":
            m = str(args.get("mode", "")).upper()
            if m not in MODES:
                return {"error": "mode must be " + "|".join(MODES)}, True
            if m == "LOW_POWER_SLEEP":
                return {"mode": m, "sleep_s": args.get("duration_s", 300),
                        "note": "emulator does not sleep; staying ACTIVE"}, False
            self.mode = m
            if m == "FAILSAFE":
                self.act = {c: (0 if v is not None else None) for c, v in self.act.items()}
            return {"mode": m}, False
        if name == "identify":
            print(f"[emu] {self.id}: *blink blink*")
            return {"ok": True, "display": False}, False
        return None, True

    def rpc(self, req: dict) -> dict:
        rid, m = req.get("id"), req.get("method")
        if m == "initialize":
            return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "ionity-esp32-node", "version": FW}}}
        if m == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
        if m == "tools/call":
            p = req.get("params") or {}
            data, is_err = self.call_tool(p.get("name"), p.get("arguments") or {})
            if data is None:
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"Unknown tool '{p.get('name')}'"}}
            res = {"content": [{"type": "text", "text": json.dumps(data)}], "isError": is_err}
            if not is_err:
                res["structuredContent"] = data
            return {"jsonrpc": "2.0", "id": rid, "result": res}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {m}"}}

    def reply(self, cmd_id, ok, detail):
        self.c.publish(self.base + "cmd/result", json.dumps(
            {"device_id": self.id, "cmd_id": cmd_id, "ok": ok, "detail": detail}), qos=1)

    def on_message(self, c, u, msg):
        try:
            cmd = json.loads(msg.payload)
        except ValueError:
            return
        action, cid = cmd.get("action"), cmd.get("cmd_id", "")
        print(f"[emu] cmd <- {action}")
        if action == "mcp":
            resp = self.rpc(cmd.get("rpc") or {})
            self.reply(cid, "error" not in resp, json.dumps(resp))
        elif action == "ping":
            self.reply(cid, True, "pong")
        elif action == "set_state_mode":
            d, err = self.call_tool("set_state_mode", cmd)
            self.reply(cid, not err, json.dumps(d))
        elif action == "identify":
            self.reply(cid, True, "blinked")
        else:
            self.reply(cid, False, "unknown action (emulator)")

    def run(self):
        self.c.connect(self.a.mqtt_host, self.a.mqtt_port, 60)
        self.c.loop_start()
        last_tel = 0.0
        try:
            while True:
                self.sample()
                every = 60 if self.mode == "STANDBY" else self.a.interval
                if time.time() - last_tel >= every:
                    last_tel = time.time()
                    d = self.telemetry_doc()
                    m = {k: v for k, v in d["metrics"].items()}
                    m["state_mode"] = MODES.index(self.mode)
                    if self.mode == "INFERENCE_ACTIVE":
                        r, _ = self.infer("anomaly_zscore")
                        if r:
                            m["inf_confidence"], m["inf_score"] = round(r["confidence"], 3), round(r["score"], 3)
                            m["inf_positive"] = 1 if r["label"] == "anomaly" else 0
                    self.tx += 1
                    self.c.publish(self.base + "telemetry", json.dumps({
                        "device_id": self.id, "site": self.site, "group": self.group, "label": self.a.label,
                        "fw": FW, "product": "ionity-esp32-mcp-node", "uptime_s": d["uptime_s"], "seq": self.tx,
                        "metrics": m, "mode": self.mode,
                        "net": {"ip": "10.0.0.42", "transport": "mqtt", "server": self.a.mqtt_host,
                                "resolved_by": "emulator", "mcp": "mqtt-only (emulator)"}}), qos=0)
                time.sleep(0.25)
        except KeyboardInterrupt:
            pass
        finally:
            self.c.publish(self.base + "status", json.dumps(self.status("offline")), qos=1, retain=True)
            time.sleep(0.3)
            self.c.loop_stop()
            print(f"[emu] {self.id} stopped")


def main():
    p = argparse.ArgumentParser(description="Ionity fw 2.0 board emulator")
    p.add_argument("--id", default="esp32-emu000000001")
    p.add_argument("--site", default="lab")
    p.add_argument("--group", default="emulated")
    p.add_argument("--label", default="fw2 emulator")
    p.add_argument("--mqtt-host", default="127.0.0.1")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--interval", type=float, default=10.0)
    Board(p.parse_args()).run()


if __name__ == "__main__":
    main()
