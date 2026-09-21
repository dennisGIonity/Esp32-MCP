<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-004 | v1.0.0 | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# Telemetry contract

One contract, two transports. Identical JSON whether it arrives over MQTT or HTTP.

## Topics

```
ionity/<site>/<device_id>/telemetry     device -> server   QoS 0
ionity/<site>/<device_id>/status        device -> server   QoS 1, retained + LWT
ionity/<site>/<device_id>/cmd           server -> device   QoS 1
ionity/<site>/<device_id>/cmd/result    device -> server   QoS 1
ionity/broadcast/cmd                    server -> all      QoS 1
```

Server subscribes with wildcards (`ionity/+/+/telemetry`), so device 1001
needs no server change.

## Telemetry payload

```json
{
  "device_id": "esp32-a1b2c3d4e5f6",
  "site": "kelvin-drive",
  "group": "power",
  "label": "GF riser",
  "fw": "1.0.0",
  "product": "ionity-esp32-mcp-node",
  "uptime_s": 84213,
  "seq": 8421,
  "metrics": {
    "temp_c": 31.4,
    "rssi_dbm": -62,
    "analog_v": 1.842,
    "digital_state": 1,
    "free_heap_bytes": 184320,
    "latency_ms": 12.4,
    "packet_loss_pct": 0
  },
  "net": { "ip": "192.168.2.57", "transport": "mqtt" }
}
```

### Rules

- `device_id` is the only required field. Everything else has a default.
- **`metrics` is open.** Any key you add is stored. Numeric and boolean values
  (bool → 1/0) also land in the indexed `telemetry_metric` table and become
  queryable via `query_telemetry` / `aggregate_metric` immediately.
- String metric values are kept in the JSON blob but are not time-series
  indexed — use them for state labels, not for charting.
- `ts` is optional. ESP32s have no RTC by default, so the server stamps
  arrival time. Send `ts` (unix seconds, float) if your node has one.
- Keep a payload under 1 KB. `MQTT_MAX_PACKET_SIZE` is set to 1024.

### Reserved metric names

These drive the built-in alert engine. Reuse them with the same units.

| Metric | Unit | Alert when |
|---|---|---|
| `temp_c` | °C | `> IONITY_ALERT_TEMP_C` (default 80) |
| `rssi_dbm` | dBm | `< IONITY_ALERT_RSSI_DBM` (default −85) |
| `packet_loss_pct` | % | `> IONITY_ALERT_PACKET_LOSS_PCT` (default 20) |
| `free_heap_bytes` | bytes | `< IONITY_ALERT_FREE_HEAP_BYTES` (default 20000) |

Anything else is stored and charted but not alerted on. To add an alert, append
one tuple to `checks` in `server/app/fleet/registry.py:_evaluate_alerts`.

## Status payload (retained + Last Will)

```json
{
  "device_id": "esp32-a1b2c3d4e5f6", "site": "kelvin-drive", "group": "power",
  "label": "GF riser", "fw": "1.0.0", "state": "online",
  "ip": "192.168.2.57", "uptime_s": 84213,
  "tx_ok": 8410, "tx_fail": 11, "transport": "mqtt"
}
```

Published retained on connect with `state:"online"`, and registered as the
Last Will with `state:"offline"` — the broker publishes it if the node stops
responding, so a dead device is visible in ~90 s without polling.

## Command payload

```json
{ "action": "set_meta", "cmd_id": "c1758441000123",
  "site": "kelvin-drive", "group": "power", "label": "GF riser" }
```

| Action | Effect |
|---|---|
| `identify` | Blinks the heartbeat LED ~1.2 s. Find a unit in a rack. |
| `ping` | Replies `pong` on `cmd/result`. Liveness check. |
| `reboot` | `ESP.restart()`. |
| `set_meta` | Writes site/group/label to NVS and reboots to re-topic. |

Result on `cmd/result`:

```json
{ "device_id": "esp32-…", "cmd_id": "c1758441000123",
  "ok": true, "detail": "metadata stored; rebooting to re-topic" }
```

## Health states

| State | Condition |
|---|---|
| `online` | last reading < `IONITY_STALE_AFTER_S` (45 s) and no open alerts |
| `alerting` | reporting normally but has ≥1 open alert |
| `stale` | 45 s – 135 s since last reading |
| `offline` | > `IONITY_OFFLINE_AFTER_S` (135 s), or LWT fired |

Windows assume a 10 s telemetry interval — roughly 4 and 13 missed readings.
Adjust both if you change `TELEMETRY_INTERVAL_MS`.

---

*Governance: Policy 986 AED · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd*
