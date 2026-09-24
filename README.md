<!--
========================================================================================
AEDI - IONITY GLOBAL - ESP32-MCP FLEET PLATFORM
Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | AEDI
Document ID: DOC-2026-09-ESP32MCP-001 | Version: 1.0.0 | Updated: 2026-09-21 SAST
Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved - TM2
Web: https://www.ionity.today | https://www.ionity.world | Ref: https://www.ionity.co.za
Classification: PUBLIC | Building Tomorrow, Today. | Anything is Possible with God.
========================================================================================
-->

# Ionity ESP32-MCP — Fleet Telemetry & Model Context Protocol Gateway

One binary on the device. One server for the fleet. One MCP endpoint for the AI.

Scales from the first board on your desk to **1000+ ESP32 nodes** reporting into
the fleet host, with a live dashboard and a Model Context Protocol surface so
Claude / AEDi can query and command the whole fleet as a single system.

> **Live lab:** three real boards (2× ESP32-S3 over MQTT, 1× Pico 2 over the
> serial bridge) report into `site=lab`. The lab itself — isolated network, shared
> MQTT broker, Pi 5 tools, health check — is its own project,
> **[Ionity-2nd-Router-Test-Lab](https://github.com/dennisGIonity/Ionity-2nd-Router-Test-Lab)** (private, `E:\.IONITY-LAB`),
> and ESP32-MCP is registered in it. Start everything with `E:\.IONITY-LAB\lab.ps1 start`
> (or just this project with `scripts\start_lab.ps1`, which runs at logon). See **[docs/LAB.md](docs/LAB.md)**.
>
> Boards find the server as **`ionity-fleet.local`** over mDNS, not by a fixed
> IP — a router swap moved the LAN once already and the fleet followed.

---

## What this is

| Layer | What it does |
|---|---|
| **Firmware** (`firmware/`) | PlatformIO / Arduino. MQTT primary, HTTP fallback, MAC-derived device id, NVS provisioning, LWT, offline ring buffer, OTA. |
| **Fleet server** (`server/`) | FastAPI. MQTT bridge + HTTP ingest → in-memory registry → batched SQLite writes. Alert engine, WebSocket broadcaster. |
| **MCP gateway** (`server/app/mcp/`) | JSON-RPC 2.0, over HTTP *and* stdio. Seven tools covering the whole fleet. |
| **Dashboard** (`dashboard/`) | Live fleet monitor: KPIs, ingest rate, health bar, device grid, drill-down, alerts, MCP console. |
| **Simulator** (`scripts/`) | Fakes N devices so you can prove the stack before flashing hardware. |
| **Reference** (`_reference/`) | `E:\.RouterProject` imported verbatim. See [`docs/REUSE-AUDIT.md`](docs/REUSE-AUDIT.md). |

---

## Quick start — no hardware needed

```powershell
cd E:\.ESP32-MCP

# 1. Python deps
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r server\requirements.txt
# Windows Smart App Control blocks zeroconf's compiled DLLs (mDNS silently off).
# Reinstall it as pure Python so ionity-fleet.local is advertised:
$env:SKIP_CYTHON = '1'; pip install --force-reinstall --no-deps --no-binary zeroconf zeroconf

# 2. Start the fleet server (dashboard + API + MCP on :8099)
python server\run.py

# 3. In a second terminal: pretend to be 250 ESP32s
.\.venv\Scripts\Activate.ps1
python scripts\fleet_simulator.py --devices 250 --interval 10
```

Open **http://localhost:8099/** (or the host's LAN IP, e.g. `http://192.168.0.3:8099/`).

MQTT is optional for the simulator's HTTP mode — devices fall back to HTTP
ingest and the server keeps working.

### With the broker (the real path)

```powershell
scripts\start_lab.ps1         # amqtt broker :1883 + fleet server :8099 + serial bridge
python scripts\fleet_simulator.py --devices 1000 --transport mqtt
```

`docker compose up -d mosquitto` is the production alternative (Mosquitto);
the lab uses the pure-Python broker so it does not depend on Docker Desktop.

---

## Flashing a real device

```powershell
cd E:\.ESP32-MCP\firmware
copy include\secrets.h.example include\secrets.h   # then edit WiFi + tokens
pio run -e esp32s3 -t upload -t monitor
```

The same binary flashes every unit — `device_id` comes from the eFuse MAC.
Re-tag a device's site/group/label afterwards without reflashing:

```bash
curl -X POST http://192.168.0.3:8099/api/v1/devices/esp32-a1b2c3d4e5f6/cmd \
  -H "Content-Type: application/json" \
  -d '{"action":"set_meta","site":"kelvin-drive","group":"power","label":"GF riser"}'
```

See [`docs/DEVICE-PROVISIONING.md`](docs/DEVICE-PROVISIONING.md) for the 1000-unit workflow.

---

## Wiring the MCP server to Claude

**Over HTTP** — point any MCP-over-HTTP client at:

```
POST http://192.168.0.3:8099/api/v1/mcp/rpc
```

**Over stdio** — in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ionity-esp32-fleet": {
      "command": "E:\\.ESP32-MCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "app.mcp.server"],
      "cwd": "E:\\.ESP32-MCP\\server"
    }
  }
}
```

### Tools exposed

| Tool | Use it for |
|---|---|
| `fleet_summary` | Whole-fleet health in one call. Start here. |
| `list_devices` | Filter by site / group / health / free text, paged. |
| `get_device` | One device plus recent history. |
| `query_telemetry` | Raw time-series for a metric over a window. |
| `aggregate_metric` | Mean/min/max/count + top-10 devices for a metric. |
| `get_alerts` | Open or historical alerts. |
| `send_command` | reboot / identify / ping / set_meta — one device or `broadcast`. |

Resources: `ionity://fleet/summary`, `ionity://fleet/devices`, `ionity://fleet/schema`.

---

## REST surface

```
POST /api/v1/telemetry              single reading or array (max 500)
POST /api/v1/devices/register       first-boot handshake
GET  /api/v1/fleet/summary
GET  /api/v1/devices?site=&group=&health=&search=&limit=&offset=
GET  /api/v1/devices/{id}?history=100
POST /api/v1/devices/{id}/cmd       {"action":"identify"}   ({id} may be "broadcast")
GET  /api/v1/telemetry/query?metric=temp_c&minutes=60
GET  /api/v1/telemetry/aggregate?metric=rssi_dbm&minutes=60
GET  /api/v1/alerts?open_only=true
POST /api/v1/mcp/rpc                MCP JSON-RPC 2.0
GET  /api/v1/health
WS   /ws/fleet                      live dashboard frames
```

Interactive docs: **http://192.168.0.3:8099/docs**

---

## Scale notes

| Fleet size | Interval | Load | Verdict |
|---|---|---|---|
| 100 | 10 s | 10 msg/s | trivial |
| 1 000 | 10 s | 100 msg/s | comfortable on the local box |
| 1 000 | 3 s | 333 msg/s | fine over MQTT; **do not** attempt over HTTP |
| 5 000 | 15 s | 333 msg/s | move storage to TimescaleDB (`docs/ARCHITECTURE.md`) |

Ingest is O(1) per message: the registry updates RAM immediately and a single
writer task drains a queue in batches of 200, so the database commits ~1×/sec
regardless of fleet size.

---

## Layout

```
E:\.ESP32-MCP
├── firmware/                PlatformIO project (esp32s3 | esp32dev | esp32c3 | OTA env)
├── firmware-arduino/
│   ├── Esp32_MCP_Node/      fleet node (Arduino IDE) - the one in the lab
│   ├── Pico_MCP_Node/       Pico / Pico 2 (serial or WiFi)
│   └── Esp32_Network_Sentinel/  single-site link/power sentinel with OLED clock (from Ionity-ESP32-Reporter)
├── server/                  FastAPI fleet server + MCP gateway (:8099)
│   └── app/{api,ingest,storage,fleet,mcp}
├── modules/
│   └── network-sentinel/    Kelvin Drive network sentinel service (:8000): MikroTik, load-shedding,
│                            probes, speedtest, security + traffic analysis, its own MCP + dashboard
├── dashboard/               fleet dashboard served at /
├── infra/                   Dockerfile + mosquitto.conf
├── scripts/                 start_lab, add_device, serial bridge, simulator
└── docs/                    architecture, reuse audit, schema, provisioning, lab, roadmap
```

> **One repo.** `Ionity-ESP32-Reporter` (formerly `E:\.RouterProject`) was merged in on 2026-09-25:
> its service lives in `modules/network-sentinel`, its firmware in `firmware-arduino/Esp32_Network_Sentinel`.
> The lab (network, broker, Pi tools) is separate: `github.com/dennisGIonity/Ionity-2nd-Router-Test-Lab`.

---

*Governance: Policy 986 AED · Licence AED 900 · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd · TM2*
