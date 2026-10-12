#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | ESP32-MCP fleet load simulator

Pretends to be N ESP32 nodes so you can build and prove the server and
dashboard before a single board is flashed. Same JSON contract as the
firmware, so anything that works here works with real hardware.

    python scripts/fleet_simulator.py --devices 1000 --interval 10
    python scripts/fleet_simulator.py --devices 50 --transport mqtt
    python scripts/fleet_simulator.py --devices 200 --fault-rate 0.08

(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Policy 986 AED
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import time

SITES = ["ionity-local", "kelvin-drive", "centurion-hq", "warehouse-a"]
GROUPS = ["sensors", "power", "network-probe", "cold-chain"]


def make_devices(n: int) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        out.append({
            "device_id": f"esp32-{i:012x}",
            "site": SITES[i % len(SITES)],
            "group": GROUPS[i % len(GROUPS)],
            "label": f"node-{i:04d}",
            "fw": "1.0.0" if i % 17 else "0.9.3",
            "ip": f"192.168.{2 + (i // 250)}.{(i % 250) + 2}",
            "base_temp": 26 + random.uniform(-4, 9),
            "base_rssi": -48 - random.uniform(0, 34),
            "boot": time.time() - random.uniform(60, 900_000),
            "seq": 0,
        })
    return out


def reading(d: dict, t: float, fault: bool) -> dict:
    phase = (t / 120.0) + hash(d["device_id"]) % 100
    temp = d["base_temp"] + 2.4 * math.sin(phase) + random.uniform(-0.4, 0.4)
    rssi = d["base_rssi"] + random.uniform(-4, 4)
    loss = random.choice([0, 0, 0, 0, 25, 50]) if fault else random.choice([0, 0, 0, 0, 0, 25])
    heap = random.randint(14_000, 210_000) if fault else random.randint(120_000, 260_000)
    if fault:
        temp += random.uniform(45, 70)
        rssi -= random.uniform(25, 45)

    d["seq"] += 1
    return {
        "device_id": d["device_id"], "site": d["site"], "group": d["group"],
        "label": d["label"], "fw": d["fw"], "product": "ionity-esp32-mcp-node",
        "uptime_s": int(t - d["boot"]), "seq": d["seq"],
        "metrics": {
            "temp_c": round(temp, 1),
            "rssi_dbm": round(rssi, 1),
            "analog_v": round(random.uniform(0.2, 3.25), 3),
            "digital_state": random.choice([0, 1]),
            "free_heap_bytes": heap,
            "latency_ms": round(random.uniform(3, 45) + (120 if fault else 0), 1),
            "packet_loss_pct": loss,
        },
        "net": {"ip": d["ip"], "transport": "sim"},
    }


async def run_http(devices, args):
    import httpx

    url = f"http://{args.host}:{args.port}/api/v1/telemetry"
    headers = {"X-Fleet-Token": args.token, "Content-Type": "application/json"}
    sent = errors = 0
    t0 = time.time()

    async with httpx.AsyncClient(timeout=20.0) as client:
        while True:
            t = time.time()
            batch = [
                reading(d, t, random.random() < args.fault_rate) for d in devices
            ]
            # chunk to respect the 500-per-request server limit
            for i in range(0, len(batch), 400):
                chunk = batch[i:i + 400]
                try:
                    r = await client.post(url, headers=headers, json=chunk)
                    if r.status_code < 300:
                        sent += len(chunk)
                    else:
                        errors += 1
                        print(f"  HTTP {r.status_code}: {r.text[:160]}")
                except Exception as e:
                    errors += 1
                    print(f"  post failed: {e}")

            el = time.time() - t0
            print(f"[sim] {len(devices)} devices | sent={sent} "
                  f"errors={errors} | {sent/max(el,1):.0f} msg/s avg")
            if args.once:
                return
            await asyncio.sleep(args.interval)


async def run_mqtt(devices, args):
    import aiomqtt

    sent = 0
    t0 = time.time()
    kwargs = {}
    if args.mqtt_user:
        kwargs["username"] = args.mqtt_user
        kwargs["password"] = args.mqtt_pass

    async with aiomqtt.Client(hostname=args.mqtt_host, port=args.mqtt_port,
                              identifier="ionity-simulator", **kwargs) as client:
        # announce everyone online (retained), same as real firmware
        for d in devices:
            await client.publish(
                f"ionity/{d['site']}/{d['device_id']}/status",
                json.dumps({"device_id": d["device_id"], "site": d["site"],
                            "group": d["group"], "label": d["label"],
                            "fw": d["fw"], "state": "online", "ip": d["ip"],
                            "transport": "mqtt"}),
                qos=1, retain=True)

        while True:
            t = time.time()
            for d in devices:
                payload = reading(d, t, random.random() < args.fault_rate)
                await client.publish(
                    f"ionity/{d['site']}/{d['device_id']}/telemetry",
                    json.dumps(payload), qos=0)
                sent += 1
            el = time.time() - t0
            print(f"[sim] {len(devices)} devices | sent={sent} | "
                  f"{sent/max(el,1):.0f} msg/s avg")
            if args.once:
                return
            await asyncio.sleep(args.interval)


def main() -> None:
    p = argparse.ArgumentParser(description="Ionity ESP32-MCP fleet simulator")
    p.add_argument("--devices", type=int, default=100)
    p.add_argument("--interval", type=float, default=10.0, help="seconds between rounds")
    p.add_argument("--transport", choices=["http", "mqtt"], default="http")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8099)
    p.add_argument("--token", default="dev-fleet-token-change-me")
    p.add_argument("--mqtt-host", default="127.0.0.1")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-user", default="")
    p.add_argument("--mqtt-pass", default="")
    p.add_argument("--fault-rate", type=float, default=0.03,
                   help="fraction of readings that trip an alert threshold")
    p.add_argument("--once", action="store_true", help="send one round and exit")
    args = p.parse_args()

    devices = make_devices(args.devices)
    print(f"[sim] simulating {args.devices} ESP32 nodes over {args.transport} "
          f"every {args.interval}s (fault rate {args.fault_rate:.0%})")
    runner = run_mqtt if args.transport == "mqtt" else run_http
    # aiomqtt needs add_reader/add_writer, which Windows' default Proactor
    # loop does not implement. A Selector loop is fine for this script.
    kwargs = {}
    if sys.platform == "win32" and args.transport == "mqtt":
        kwargs["loop_factory"] = asyncio.SelectorEventLoop
    try:
        asyncio.run(runner(devices, args), **kwargs)
    except KeyboardInterrupt:
        print("\n[sim] stopped")


if __name__ == "__main__":
    main()
