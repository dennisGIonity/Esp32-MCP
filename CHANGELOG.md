<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-CHG | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# Changelog

## 2.0.0 — 2026-09-29 (`revamp/v2`)

### Firmware 2.0.0 (`firmware-arduino/Esp32_MCP_Node`)
- **Provisioning without recompiling:** WiFi, MCP host (IP, DNS name or `.local`), ports, MQTT user,
  fleet token, MCP token, OTA password, role, site/group/label and actuator pins live in NVS and are
  written over USB with the `ionity-prov/1` line protocol. `secrets.h` is optional and only seeds the
  first boot. An unprovisioned board waits on serial instead of looping on an empty SSID.
- **On-device MCP server:** `initialize`, `tools/list`, `tools/call` over HTTP (`:80/mcp`, bearer
  `mcp_token` for writes) and over MQTT (`action: "mcp"`). Tools: `get_device_info`,
  `read_telemetry`, `run_inference`, `set_actuator`, `set_state_mode`, `identify`.
- **Edge inference:** `anomaly_zscore`, `rssi_motion`, `analog_threshold` on 64-sample rings.
- **State modes:** ACTIVE / STANDBY / INFERENCE_ACTIVE / LOW_POWER_SLEEP (announced, reply-then-sleep)
  / FAILSAFE (outputs locked, latched across reboot).
- **Actuators:** LED, alert LED, 2× PWM, relay; pins claimed on first use; unsafe pins refused.
- Telemetry adds `min_free_heap_bytes`, `loop_us_avg`, `loop_us_max`, `state_mode`, `inf_*`; status
  adds `mode`, `mcp`, and the new `sleeping` state. MQTT buffer 4 KB.
- OTA only starts when an OTA password is provisioned (1.x used a compiled default).
- Flasher images: `esp32s3_uart`, `esp32s3_usb`, `esp32_classic`, `esp32c3_usb` via `firmware/build.py`.

### Flasher (`flasher/`, new)
- React + esptool-js 0.7 + Web Serial. Chip + USB-bridge detection → right image, sha256 check,
  full erase or **update-only** (skips NVS), provisioning, WiFi test, host-side verification incl. the
  board's MCP tools. Served at `/flasher/`; GitHub Pages build with bundled images.

### Fleet host / MCP 2.0.0 (`server/`)
- New tools `device_list_tools`, `device_call_tool` (read tools open, write tools behind the admin
  token), `integrations_status`; `send_command` gains `set_state_mode`.
- `POST /api/v1/devices/{id}/mcp`, `/api/v1/firmware/*`, `/api/v1/provisioning/defaults`, `/api/v1/integrations`.
- **Datadog forwarder:** metrics, fleet rollups, alert/state/inference events, `can_connect` checks;
  batching, retry and back-off; off without a key.
- **Fixed:** SQLite WAL grew without bound (150 MB) → truncated on start and after every prune.
- **Fixed:** a pinned `IONITY_MDNS_ADVERTISE_IP` that the host no longer has sent the whole fleet to a
  dead address → falls back to the routed LAN IP with a warning (also used by the flasher defaults).
- **Fixed:** shutdown / test hang (writer cancelled mid-query deadlocked aiosqlite).
- Devices report `mode`, `mcp_url`, `sleeping_until`; sleeping boards are not treated as crashed.

### Tooling
- `scripts/device_emulator.py` (a fw 2.0 board incl. MCP over MQTT), `scripts/provision.py` (CLI provisioning).
- GitHub Actions: server tests, sentinel tests, flasher typecheck/test/build, firmware matrix, Pages + release.
- Dashboard links to the flasher.

## 1.3.0 — 2026-09-28
MCP protocol negotiation, prompts, argument validation, annotations, admin token, Pi agent.
