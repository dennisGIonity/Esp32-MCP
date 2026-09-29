<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-DMCP | v2.0.0 | 2026-09-29 SAST | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# On-device MCP server (fw 2.0)

Every node is its own Model Context Protocol server. Implementation:
`firmware-arduino/Esp32_MCP_Node/DeviceMcp.ino` (dispatcher + HTTP), `EdgeAI.ino` (models, state modes),
`Actuators.ino` (outputs).

## Transports

| Transport | How | Auth |
|---|---|---|
| HTTP | `POST http://<board-ip>/mcp` — JSON-RPC 2.0, JSON responses (Streamable-HTTP without SSE). `GET /info` = device info. mDNS service `_ionity-mcp._tcp`. | With no `mcp_token`: read tools only. With one: every request needs `Authorization: Bearer <mcp_token>`, which also unlocks write tools. |
| MQTT (via the host) | Host publishes `{"action":"mcp","cmd_id":"…","rpc":{…}}` to `ionity/<site>/<id>/cmd`; the board answers on `…/cmd/result` with the JSON-RPC response in `detail`. The host's `device_call_tool` / `device_list_tools` and `POST /api/v1/devices/{id}/mcp` wrap this. | The host enforces `IONITY_ADMIN_TOKEN` for write tools. |

Methods: `initialize` (protocol 2025-06-18 / 2025-03-26 / 2024-11-05), `notifications/initialized`,
`ping`, `tools/list`, `tools/call`. Batches are refused.

## Tools

| Tool | Args | Returns |
|---|---|---|
| `get_device_info` | – | device_id, label/site/group, fw, chip, rev, cores, MHz, flash, PSRAM, SDK, MAC, role, mode, inference model, host, MCP URL, OLED, tool list |
| `read_telemetry` | – | `metrics` (temp_c, analog_v, digital_state, rssi_dbm, free/min_free/max_alloc heap, PSRAM, loop_us_avg/max, freertos_tasks, loop_stack_free_bytes), `net`, `actuators`, `last_inference`, counters |
| `run_inference` | `model_id` (anomaly_zscore \| rssi_motion \| analog_threshold), `input_frame` (≤ 64 numbers, optional), `threshold` | label, confidence 0..1, score, samples, source (live/input_frame), elapsed_us |
| `set_actuator` | `channel` (led \| alert_led \| pwm0 \| pwm1 \| relay0), `value` 0..1 | channel, value, mode — refused in FAILSAFE |
| `set_state_mode` | `mode`, `duration_s` (sleep), `model_id` (for INFERENCE_ACTIVE) | new mode; LOW_POWER_SLEEP answers first, then sleeps |
| `identify` | – | blinks LED, flashes OLED |

## Edge models

Live input: 64-sample rings filled every 250 ms (ADC volts, RSSI dBm).

* **anomaly_zscore** — z of the newest sample against the others; `|z| > threshold` (3) → `anomaly`.
* **rssi_motion** — std-dev of RSSI; `> threshold` (2.5 dB) → `motion`. A coarse presence signal from
  WiFi signal jitter, not CSI.
* **analog_threshold** — newest ADC value vs `threshold` (1.65 V) → `above` / `below`.

In `INFERENCE_ACTIVE` the chosen model runs every 2 s; telemetry gains `inf_confidence`, `inf_score`,
`inf_positive`, and each transition to positive is sent to the host as an inference event (Datadog too).
A TFLite Micro / ESP-DL model is added as another `model_id` case in `runInference()`, returning the same
`Inference` struct.

## State modes

| Mode | Telemetry | Notes |
|---|---|---|
| ACTIVE | 10 s | default |
| STANDBY | 60 s | slower loop, no inference |
| INFERENCE_ACTIVE | 10 s + inf_* | model every 2 s |
| LOW_POWER_SLEEP | – | deep sleep `duration_s` (5–86400, default 300); retained status `sleeping` so the host does not call it a crash; wakes as ACTIVE |
| FAILSAFE | 10 s | all actuators 0 and locked; latched in NVS across reboots until set back |

## Actuator pins

| Chip | pwm0 | pwm1 | relay0 |
|---|---|---|---|
| ESP32-S3 / S2 | 5 | 6 | 7 |
| ESP32 classic | 25 | 26 | 27 |
| ESP32-C3 | 4 | 5 | 10 |

Override per board with the flasher's `pins` field. Flash/PSRAM, native-USB, UART0, strapping and
input-only pins are refused. A pin is not touched until its channel is first driven.

## Example

```bash
curl -s http://192.168.0.42/mcp -H 'Content-Type: application/json' -H "Authorization: Bearer $MCP_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"set_actuator","arguments":{"channel":"pwm0","value":0.35}}}'

# the same through the fleet host, no board IP needed
curl -s http://ionity-fleet.local:8099/api/v1/devices/esp32-98a316e5d18c/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"read_telemetry","arguments":{}}}'
```

`scripts/device_emulator.py` answers the same JSON-RPC over MQTT, for testing without hardware.
