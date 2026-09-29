<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-DD | v2.0.0 | 2026-09-29 SAST | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# Datadog

The fleet host forwards; boards never hold an API key. Code: `server/app/integrations/datadog.py`.

## Enable

```ini
IONITY_DD_ENABLED=true
IONITY_DD_API_KEY=...            # Organization settings -> API keys (not an application key)
IONITY_DD_SITE=datadoghq.eu      # datadoghq.com | us3.datadoghq.com | us5.datadoghq.com | ap1.datadoghq.com | datadoghq.eu
IONITY_DD_ENV=lab
IONITY_DD_TAGS=team:iot,region:za
IONITY_DD_FLUSH_S=15
```

Restart the host. `GET /api/v1/integrations` or the MCP tool `integrations_status` shows points/events/checks
sent and the last error. A bad key backs off (15 s doubling to 5 min) and keeps the queue.

## What arrives

| Kind | Name | Detail |
|---|---|---|
| Metric (gauge) | `ionity.esp32.<metric>` | every numeric telemetry field (temp_c, rssi_dbm, free_heap_bytes, loop_us_max, inf_score, …); host = device_id; tags `device_id site group fw transport env service` + IONITY_DD_TAGS |
| Metric | `ionity.fleet.devices{health:online\|stale\|offline\|alerting}`, `ionity.fleet.devices.total`, `ionity.fleet.ingest_rate`, `ionity.fleet.alerts.open` | each flush |
| Event | `[ALERT] <id>: <code>` / `[CLEARED] …` | low_rssi, packet_loss, over_temp, low_heap |
| Event | `<id> is online / offline / sleeping`, `inference:<label>` | from status, Last Will and edge events |
| Service check | `ionity.esp32.can_connect` | OK online/alerting, WARNING stale, CRITICAL offline |

## Suggested monitors

* `ionity.esp32.can_connect` by `device_id` — CRITICAL for 5 min → page.
* `min:ionity.esp32.min_free_heap_bytes{*} by {device_id} < 30000` — heap leak before it crashes.
* `max:ionity.esp32.loop_us_max{*} by {device_id} > 200000` — a blocking call stalled the loop.
* `avg:ionity.esp32.rssi_dbm{*} by {device_id} < -80` for 15 min — board needs a better AP.
* Events `sources:ionity-esp32-mcp alert_type:error` → Slack.

## Datadog MCP

The Datadog MCP connector (engineering plugin) lets the same AI query these metrics and monitors. It needs
to be authorised once in the Claude connector settings.
