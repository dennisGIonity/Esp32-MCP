<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-DEPLOY | v1.0.0 | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# Deployables: what runs on which device

Every node speaks one protocol, so the fleet server, dashboard and MCP tools treat them all the same.

| Device | What to deploy | Transport | Commands | Deploy with |
|---|---|---|---|---|
| **ESP32-S3 / ESP32 / ESP32-C3** | `firmware-arduino/Esp32_MCP_Node` (fw 1.2.0) | WiFi → MQTT (HTTP fallback) | all | `scripts\add_device.ps1 -Port COMx` (Arduino CLI) · or Arduino IDE · or `firmware/` PlatformIO |
| **Raspberry Pi Pico 2 / Pico** (no radio) | `firmware-arduino/Pico_MCP_Node` (fw 1.1.0) | USB serial → `scripts/serial_bridge.py` → HTTP | ping, identify, dns_probe (via host) | Arduino IDE, board *Raspberry Pi Pico 2* |
| **Pico W / Pico 2 W** | same sketch, W board selected | WiFi → HTTP + serial | via serial bridge | Arduino IDE, board *Pico 2 W* |
| **Raspberry Pi Zero W / Zero 2 W / any Linux SBC** | `devices/pi-agent` (agent 1.0.0) | WiFi/Ethernet → MQTT (HTTP fallback) | ping, identify (ACT LED), reboot, set_meta, dns_probe | `sudo bash install.sh` on the Pi → systemd service |
| **ESP32-S3 network sentinel** (Kelvin Drive) | `firmware-arduino/Esp32_Network_Sentinel` | HTTP → `modules/network-sentinel` (:8000) | — | Arduino IDE |

## The protocol (all fleet nodes)
```
<root>/<site>/<device_id>/telemetry      JSON reading, every ~10 s       (device -> server)
<root>/<site>/<device_id>/status         retained online/offline + Last Will
<root>/<site>/<device_id>/cmd            {"action": ..., "cmd_id": ...}   (server -> device)
<root>/broadcast/cmd                     same, to every node
<root>/<site>/<device_id>/cmd/result     {"device_id","cmd_id","ok","detail"}
HTTP fallback: POST http://<server>:8099/api/v1/telemetry  (X-Fleet-Token)
```
`root` = `ionity`. `device_id` always comes from the silicon: `esp32-<efuse mac>`,
`pico-<chip id>`, `pi-<cpu serial>`. The metrics map is open-ended: a new metric is stored,
charted and MCP-queryable with no server change.

## Finding the server (every node, same order)
1. a pinned address (ESP32: NVS `server_ip`; Pi: `server =` in `/etc/ionity-agent.conf`)
2. mDNS **`ionity-fleet.local`**, advertised by the fleet server
3. fallback `192.168.124.4`, the laptop's pinned address in the lab

## WiFi credentials
Only in git-ignored files: `firmware-arduino/*/secrets.h` (write them all at once with the lab's
`SET-LAB-WIFI.cmd`). The Pi uses the WiFi it already has (Raspberry Pi Imager / `nmcli`).

## Securing a deployment beyond the lab
| Setting (`.env`) | Effect |
|---|---|
| `IONITY_ADMIN_TOKEN=<random>` | commands (REST `/cmd`, MCP `send_command`) need `Authorization: Bearer <token>`; the MCP bridge reads it from `.env`, the dashboard asks once |
| `IONITY_REQUIRE_TOKEN=true` + `IONITY_FLEET_TOKEN` | HTTP ingest needs `X-Fleet-Token` (set the same token in each node's `secrets.h` / Pi config) |
| `IONITY_MQTT_USERNAME` / `_PASSWORD` | once the broker requires auth (the lab broker is anonymous, lab network only) |

## Verifying a node
- MCP: `get_device {"device_id": "..."}` → `health: online`, `fw`, `transport`, `metrics`
- `send_command {"device_id": "...", "action": "ping"}` → `get_command_results` shows `pong`
- Lab health check: `E:\.claude\Ionity\.IONITY-LAB\LAB-STATUS.cmd`
