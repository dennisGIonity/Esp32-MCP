<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-003 | v1.0.0 | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# Architecture

```
  1000x ESP32 nodes                    Ionity Local Drive host (192.168.2.11)
 ┌────────────────────┐
 │ firmware/          │   MQTT 1883    ┌──────────────────────────────────────┐
 │  device_id = MAC   ├───────────────►│  Mosquitto                           │
 │  site/group = NVS  │  ionity/+/+/#  │  ionity/<site>/<dev>/telemetry       │
 │  MQTT primary      │◄───────────────┤                      /status  (LWT)  │
 │  HTTP fallback     │   cmd topic    │                      /cmd            │
 │  40-slot buffer    │                └───────────────┬──────────────────────┘
 │  OTA               │                                │ wildcard subscribe
 └─────────┬──────────┘                                ▼
           │  POST /api/v1/telemetry     ┌──────────────────────────────────┐
           └────────────────────────────►│  MqttBridge / HTTP ingest        │
             (only when broker is down)  └───────────────┬──────────────────┘
                                                         ▼
                                         ┌──────────────────────────────────┐
                                         │  FleetRegistry  (hot, in RAM)    │
                                         │   devices{}  ~1 KB each          │
                                         │   asyncio.Queue -> writer task   │
                                         │   alert engine                   │
                                         └───┬───────────────┬──────────────┘
                                             │               │
                          batched 200/commit ▼               ▼ 2 s frames
                                   ┌───────────────┐  ┌──────────────────┐
                                   │ SQLiteStore   │  │ WS /ws/fleet     │
                                   │ (Store iface) │  │  -> dashboard    │
                                   └───────┬───────┘  └──────────────────┘
                                           │
                        ┌──────────────────┴──────────────────┐
                        ▼                                     ▼
              ┌──────────────────┐                  ┌──────────────────┐
              │ REST /api/v1/... │                  │ MCP JSON-RPC     │
              └──────────────────┘                  │  HTTP + stdio    │
                                                    │  -> Claude/AEDi  │
                                                    └──────────────────┘
```

## Why these choices

**MQTT primary.** One persistent TCP connection per device instead of a TLS
handshake per reading. At 1000 devices the difference is 100 steady
connections versus 100–333 new sockets per second. The broker also gives us
Last Will and Testament: when a node dies, the broker publishes its offline
status within the keepalive window — we learn about it in ~90 s without
polling anything.

**HTTP fallback, not HTTP-only.** A broker outage should degrade the fleet,
not stop it. After three failed MQTT connects a node flips to
`POST /api/v1/telemetry` and flips back automatically when the broker returns.
It is also the bootstrap path for a bench device before the broker exists.

**Hot registry + batched writer.** Ingest touches RAM only. Durability is a
queue drained by one task that commits ~1×/second in batches of 200. This is
the single most important scaling decision in the codebase: without it, SQLite
serialises 100 commits/sec and the ingest path becomes the bottleneck.

**Open `metrics{}` map.** Firmware adds `soil_moisture`, the server stores it,
the dashboard charts it, and `query_telemetry`/`aggregate_metric` can query it —
with no server change and no migration. Numeric values are also written to a
narrow `telemetry_metric(device_id, ts, name, value)` table so those queries
stay indexed.

**Server-side alerting.** RouterProject evaluated thresholds on the device. At
fleet scale, changing a threshold would mean reflashing every unit. Thresholds
now live in `.env` and are evaluated on ingest.

**MCP fronting the fleet, not the device.** An agent that had to connect to
1000 MCP servers could not reason about the fleet. One gateway with
`fleet_summary` → `list_devices(health='alerting')` → `get_device` →
`send_command` is a workable agent loop.

## Storage migration path

Everything above `Store` (`server/app/storage/base.py`) is driver-agnostic.
Moving to TimescaleDB is a new `TimescaleStore(Store)` plus one config flag:

```sql
CREATE TABLE telemetry_metric (
  ts TIMESTAMPTZ NOT NULL, device_id TEXT, site TEXT, grp TEXT,
  name TEXT, value DOUBLE PRECISION);
SELECT create_hypertable('telemetry_metric','ts');
CREATE INDEX ON telemetry_metric (name, ts DESC);
ALTER TABLE telemetry_metric SET (timescaledb.compress,
  timescaledb.compress_segmentby='device_id,name');
SELECT add_compression_policy('telemetry_metric', INTERVAL '7 days');
```

Migrate when any of these is true: >2000 devices, >90 days retention wanted,
dashboard queries over multi-week windows feel slow, or you want continuous
aggregates instead of on-the-fly `AVG()`.

## Failure behaviour

| Failure | What happens |
|---|---|
| Broker down | Nodes fall back to HTTP after ~15 s. Server logs it, keeps ingesting. Dashboard header shows `mqtt down → http fallback`. |
| Server down | Nodes buffer 40 readings in RAM (~7 min at 10 s), then drop oldest. Flushed on reconnect. |
| WiFi drop | Same buffer; `WiFi.setAutoReconnect(true)` handles the rejoin. |
| Node dies | LWT fires → status `offline`. Registry marks it offline; it appears at the top of the dashboard grid. |
| Ingest queue full (20 000) | Durable writes drop, hot state and dashboard stay correct, `dropped_writes` climbs in `/api/v1/health`. This is the signal to move to TimescaleDB. |
| Disk fills | Hourly pruner enforces `IONITY_RETENTION_DAYS`. |

## Security posture (bench defaults — change before deployment)

| Setting | Bench | Deployment |
|---|---|---|
| `allow_anonymous` (mosquitto) | `true` | `false` + `passwd` file |
| `IONITY_REQUIRE_TOKEN` | `false` | `true`, rotate `FLEET_TOKEN` per batch |
| MQTT transport | plain 1883 | TLS 8883 with a fleet CA |
| Dashboard | open on the LAN | behind the Ionity Local Drive auth / reverse proxy |
| OTA | password only | password + signed images |

---

*Governance: Policy 986 AED · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd*
