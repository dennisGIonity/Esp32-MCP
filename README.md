<!--
========================================================================================
AEDI - IONITY GLOBAL - ESP32-MCP FLEET PLATFORM
Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | AEDI
Document ID: DOC-2026-09-ESP32MCP-001 | Version: 2.0.1 | Updated: 2026-10-02 SAST
Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved - TM2
Web: https://www.ionity.today | https://www.ionity.world | Ref: https://www.ionity.co.za
Classification: PUBLIC | Building Tomorrow, Today. | Anything is Possible with God.
========================================================================================
-->

# Ionity ESP32-MCP — flash, provision and command an ESP32 fleet from an AI

**One image on every board. One MCP host for the fleet. An MCP server on every board.**

```
 Claude / Ollama / AEDi ──MCP──►  Fleet host (FastAPI :8099)  ──MQTT (compact JSON-RPC)──►  ESP32 node (fw 2.0)
                                   · 17 fleet tools                                           · own MCP server:
                                   · device_call_tool bridge  ◄──telemetry / status / LWT───   read_telemetry, run_inference,
                                   · dashboard  · flasher                                      set_actuator, set_state_mode …
                                   · Datadog forwarder ──► Datadog (metrics, events, checks)   · http://<board>/mcp on the LAN
        Browser (Chrome/Edge) ── Web Serial ──► flash image + write WiFi / MCP host / tokens into NVS
```

| Layer | What it does |
|---|---|
| **Firmware 2.0** (`firmware-arduino/Esp32_MCP_Node`) | One image for any network: WiFi, MCP host and tokens live in NVS, written over USB by the flasher. MQTT primary / HTTP fallback, LWT, offline buffer, OTA. **On-device MCP server** over HTTP (`:80/mcp`) and MQTT. Edge inference, actuators, state modes. |
| **Flasher** (`flasher/`) | React + esptool-js + Web Serial. Identifies the chip, picks the right image, flashes, provisions WiFi + MCP host, checks the board joined and its MCP tools answer. Served at `/flasher/`, also built for GitHub Pages. |
| **Fleet host** (`server/`) | FastAPI. MQTT bridge + HTTP ingest → registry → batched SQLite. Alerts, WebSocket dashboard, firmware images for the flasher. |
| **MCP gateway** (`server/app/mcp/`) | JSON-RPC 2.0 over HTTP and stdio, protocol 2025-06-18. 17 tools, incl. `device_call_tool` which reaches each board's own MCP server through the broker. |
| **Datadog** (`server/app/integrations/datadog.py`) | The host forwards every metric, alert, online/offline event and a per-board service check. Boards never hold the key. |
| **Dashboard** (`dashboard/`) | Live fleet monitor, MCP console, link to the flasher. |
| **Emulator / simulator** (`scripts/`) | `device_emulator.py` = one fw 2.0 board incl. its MCP tools; `fleet_simulator.py` = N boards for load. |

> **Live lab:** 2× ESP32-S3 (MQTT) + 1× Pico 2 (serial bridge) in `site=lab`. The lab network is its own
> project, **[Ionity-2nd-Router-Test-Lab](https://github.com/dennisGIonity/Ionity-2nd-Router-Test-Lab)**
> (`E:\.IONITY-LAB`). Boards find the host as **`ionity-fleet.local`** over mDNS, or at the address the
> flasher wrote into them.

---

## Quick start

```powershell
cd E:\.ESP32-MCP
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r server\requirements.txt
python server\run.py                                   # dashboard, API, MCP, flasher on :8099
python scripts\device_emulator.py                      # a fw 2.0 board, incl. its MCP tools (needs the broker)
python scripts\fleet_simulator.py --devices 250        # load: 250 boards over HTTP
```

Dashboard **http://localhost:8099/** · Flasher **http://localhost:8099/flasher/** · API docs **/docs**.
The lab path with the broker: `scripts\start_lab.ps1` (amqtt :1883 + host + serial bridge).

### Where everything lives (lab host 192.168.0.2)

| Service | On the host | From the LAN |
|---|---|---|
| Dashboard | http://localhost:8099/ | http://192.168.0.2:8099/ · http://ionity-fleet.local:8099/ |
| Flasher (Chrome / Edge) | http://localhost:8099/flasher/ | http://192.168.0.2:8099/flasher/ (Web Serial needs localhost or https) |
| API docs · health | http://localhost:8099/docs · /api/v1/health | same on :8099 |
| MCP (HTTP) | `POST http://localhost:8099/api/v1/mcp/rpc` | `POST http://192.168.0.2:8099/api/v1/mcp/rpc` |
| MQTT broker | `mqtt://localhost:1883` | `mqtt://192.168.0.2:1883` — no web UI; e.g. MQTT Explorer, topic `ionity/#` |
| A board's own MCP (fw 2.0) | – | `POST http://<board-ip>/mcp` · `GET http://<board-ip>/info` |

Start / restart it all: `scripts\start_lab.ps1` (broker :1883 + fleet server :8099 + serial bridge; idempotent).

---

## Flash and provision a board (no recompiling)

1. `python firmware\build.py` — builds the four images (`esp32s3_uart`, `esp32s3_usb`, `esp32_classic`,
   `esp32c3_usb`) into `firmware/dist/` with a manifest (sha256, size). No `secrets.h` goes into them.
2. Open **http://localhost:8099/flasher/** in Chrome or Edge, plug the board in, then:
   **Select USB port → Flash & provision.** The flasher detects the chip (and whether it is on a
   CH340/CP210x bridge or native USB), writes the image, waits for the new firmware to say hello,
   writes WiFi + MCP host + tokens into NVS, has the board join WiFi and find the host, reboots it,
   and then confirms from the host side that it is online and its MCP tools answer.
3. Already on fw 2.0? Tick **Provision only** to move a board to another network, or
   **Update only** to flash new firmware and keep its settings (the NVS partition is skipped).

No Chrome? `python scripts\provision.py COM8 --ssid IONITY-LAB-IOT --password - --server 192.168.0.2 --mcp-token auto --test`.
Details: **[docs/FLASHER.md](docs/FLASHER.md)**. The old path (`scripts\add_device.ps1`, `secrets.h`) still works:
`secrets.h` now only seeds the first boot.

---

## The MCP layers

### 1 · Fleet host MCP (AI orchestrator + device bridge)

HTTP: `POST http://ionity-fleet.local:8099/api/v1/mcp/rpc` (Bearer `IONITY_ADMIN_TOKEN` for write tools).
stdio for Claude Desktop / Claude Code / Cowork — the bridge keeps answering while the host is down:

```json
{
  "mcpServers": {
    "ionity-esp32-fleet": {
      "command": "E:\\.ESP32-MCP\\.venv\\Scripts\\python.exe",
      "args": ["E:\\.ESP32-MCP\\server\\mcp_stdio_proxy.py"]
    }
  }
}
```

Local models (Ollama through any MCP client, e.g. `mcphost` or Open WebUI's MCP bridge) use the same HTTP endpoint.

| Tool | Use it for |
|---|---|
| `fleet_summary` | Whole-fleet health in one call. Start here. |
| `list_devices`, `get_device` | Filter / page the fleet; one board plus history (shows `mode`, `mcp_url`, `sleeping_until`). |
| `query_telemetry`, `aggregate_metric` | Time series, or mean/min/max + top-10 for a metric. |
| `get_alerts` | Open or historical alerts. |
| `send_command` ⚠ | reboot / identify / ping / set_meta / set_display / dns_probe / **set_state_mode** / **set_wifi** (rotate the fleet to a new SSID before the router changes); one board or `broadcast`. |
| `get_command_results` | Replies to commands, and edge-inference events. |
| **`device_list_tools`** | Ask one board which MCP tools it has (goes to the board). |
| **`device_call_tool`** ⚠* | Call a tool on the board's own MCP server and get its answer (~5 ms on the LAN). |
| **`integrations_status`** | Datadog forwarder + MQTT bridge counters and last error. |
| `dns_*`, `list_lan_devices` | What is going to what device on the LAN; who is on the network. |

⚠ = changes hardware; needs the admin token when one is set. \* only the write tools
(`set_actuator`, `set_state_mode`, `identify`); `read_telemetry`, `get_device_info`, `run_inference` are open.

**WiFi rescue (fw 2.1):** a board remembers the networks it has joined; if its configured SSID disappears
(router reset / rename) it joins any known network it can see, so it stays reachable and can be re-pointed
with `set_wifi` instead of a cable.

### 2 · On-device MCP (every fw 2.0 board)

`POST http://<board>/mcp` (JSON-RPC, `initialize` / `tools/list` / `tools/call`), or through the host with
`device_call_tool` — the board never has to face the internet or parse a heavy handshake.

| Board tool | What it does |
|---|---|
| `read_telemetry` | Temperature, ADC, digital in, RSSI, free / minimum-ever / largest-block heap, PSRAM, loop time avg/max (µs), FreeRTOS task count, loop stack headroom, actuators, last inference. |
| `run_inference(model_id, input_frame?, threshold?)` | On-chip models, no extra libraries: `anomaly_zscore` (ADC vs rolling window), `rssi_motion` (RSSI jitter as a coarse presence signal), `analog_threshold`. Returns label + confidence. TFLite Micro / ESP-DL models slot in as more `model_id`s. |
| `set_actuator(channel, value)` | `led`, `alert_led`, `relay0` (0/1), `pwm0`, `pwm1` (duty 0..1, 5 kHz LEDC). Pins per board in NVS; unsafe pins refused. |
| `set_state_mode(mode)` | `ACTIVE`, `STANDBY` (60 s telemetry), `INFERENCE_ACTIVE` (model every 2 s + `inf_*` metrics + events), `LOW_POWER_SLEEP` (deep sleep, announced), `FAILSAFE` (all outputs off and locked, survives reboot). |
| `get_device_info`, `identify` | Identity/config without secrets; blink LED + flash OLED. |

Writes over HTTP need the board's `mcp_token` (the flasher can generate one). Full spec:
**[docs/DEVICE-MCP.md](docs/DEVICE-MCP.md)**. Other ESP32 MCP stacks (Espressif `esp-iot-solution`
`mcp_server`, Solnera ESP32-MCPServer, Xiaozhi, `@midas/esp32-devops-mcp`) and how they fit:
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#mcp-ecosystem)**.

---

## Datadog

```ini
# .env
IONITY_DD_ENABLED=true
IONITY_DD_API_KEY=<Organization settings → API keys>
IONITY_DD_SITE=datadoghq.eu          # or datadoghq.com, us3/us5.datadoghq.com, ap1.datadoghq.com
IONITY_DD_ENV=lab
```

Every numeric metric → `ionity.esp32.<metric>` (gauge, host = device_id, tags `device_id site group fw
transport env service`), fleet rollups `ionity.fleet.*`, events for alert raised/cleared and board
online/offline/sleeping/inference, and service check `ionity.esp32.can_connect`. Dashboard + monitor
recipes: **[docs/DATADOG.md](docs/DATADOG.md)**.

---

## REST surface

```
POST /api/v1/telemetry              single reading or array (max 500)
POST /api/v1/devices/register       first-boot handshake
GET  /api/v1/fleet/summary
GET  /api/v1/devices?site=&group=&health=&search=&limit=&offset=
GET  /api/v1/devices/{id}?history=100
POST /api/v1/devices/{id}/cmd       {"action":"set_state_mode","mode":"STANDBY"}   ({id} may be "broadcast")
POST /api/v1/devices/{id}/mcp       JSON-RPC relayed to the board's own MCP server
GET  /api/v1/commands/results
GET  /api/v1/telemetry/query?metric=temp_c&minutes=60
GET  /api/v1/telemetry/aggregate?metric=rssi_dbm&minutes=60
GET  /api/v1/alerts?open_only=true
GET  /api/v1/firmware/manifest      images for the flasher
GET  /api/v1/firmware/{variant}.bin
GET  /api/v1/provisioning/defaults  host / ports / (admin) fleet token for the flasher
GET  /api/v1/integrations           Datadog + MQTT stats
POST /api/v1/mcp/rpc                MCP JSON-RPC 2.0
GET  /api/v1/health
WS   /ws/fleet                      live dashboard frames
```

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
├── firmware-arduino/
│   ├── Esp32_MCP_Node/      fw 2.0 node: main + Provision, DeviceMcp, EdgeAI, Actuators, Oled tabs
│   ├── Pico_MCP_Node/       Pico / Pico 2 (serial or WiFi)
│   └── Esp32_Network_Sentinel/  single-site link/power sentinel with OLED clock
├── firmware/
│   ├── build.py             builds the flasher images + manifest (arduino-cli)
│   └── platformio.ini       PlatformIO build of the same sketch
├── flasher/                 React web flasher (esptool-js, Web Serial) -> /flasher/ and GitHub Pages
├── server/                  FastAPI fleet host + MCP gateway (:8099)
│   └── app/{api,ingest,storage,fleet,mcp,integrations}
├── devices/pi-agent/        Raspberry Pi Zero / Linux agent + systemd installer
├── modules/network-sentinel/  Kelvin Drive network sentinel service (:8000)
├── dashboard/               fleet dashboard served at /
├── scripts/                 start_lab, add_device, provision, device_emulator, fleet_simulator, serial bridge
├── infra/                   Dockerfile + mosquitto.conf
├── .github/workflows/       CI (tests, firmware matrix, flasher) + Pages/release
└── docs/                    architecture, flasher, device MCP, Datadog, schema, provisioning, lab, roadmap
```

What changed in 2.0 and why: **[CHANGELOG.md](CHANGELOG.md)**.

---

*Governance: Policy 986 AED · Licence AED 900 · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd · TM2 ·
[www.ionity.today](https://www.ionity.today) · [www.ionity.world](https://www.ionity.world)*
