<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-006 | v1.0.0 | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# Roadmap

## Phase 0 — shipped (v1.0.0, 2026-09-21)

- One-binary firmware, MAC-derived identity, NVS provisioning
- MQTT primary + HTTP fallback, LWT, 40-slot offline buffer, OTA
- FastAPI fleet server: hot registry, batched SQLite writer, alert engine
- MCP gateway: 7 tools + 3 resources, over HTTP and stdio
- Live dashboard with device grid, drill-down, alerts and an MCP console
- Fleet simulator to 1000+ devices
- Docker compose stack (Mosquitto + server)

## Phase 1 — harden before deployment

| Item | Why |
|---|---|
| MQTT over TLS (8883) + per-batch credentials | Plain 1883 is bench-only |
| `IONITY_REQUIRE_TOKEN=true` end to end | Unauthenticated ingest is a fleet-spoofing hole |
| Dashboard behind Ionity Local Drive auth | Currently open on the LAN |
| Signed OTA images | Password-only OTA lets anyone on the LAN push firmware |
| `pytest` coverage on registry + storage + MCP dispatch | Only smoke tests today |
| Windows service wrapper (NSSM) for the server | Must survive a reboot of 192.168.2.11 |

## Phase 2 — scale

| Item | Trigger |
|---|---|
| `TimescaleStore(Store)` implementation | >2000 devices, or >90-day retention |
| Continuous aggregates (1 min / 1 h rollups) | Dashboard queries over multi-week windows |
| Dashboard virtual scrolling | >2000 devices in one view |
| Server-side downsampling for the WS frame | Currently capped at 400 devices/frame |
| Multi-broker / MQTT bridge per site | Remote sites over WAN, not one flat LAN |

## Phase 3 — capability

| Item | Note |
|---|---|
| **Eskom load-shedding enrichment** | Lift `core/loadshedding.py` from `_reference/router-project`. Stamp every reading with grid state so "device offline" can be disambiguated from "area on stage 4". High value for any SA deployment. |
| Device groups as first-class objects | Named groups with their own thresholds, rather than a free-text tag |
| Per-group alert thresholds | A cold-chain node and a network probe should not share an over-temp limit |
| Alert routing (email / Slack / webhook) | Alerts are currently visible but not pushed |
| `get_device_logs` MCP tool | Requires a device-side log ring buffer |
| MikroTik collector as an optional site module | Re-adopt `core/mikrotik_collector.py` if a fleet site has a CRS326 |
| Firmware rollout orchestration in the dashboard | Staged OTA waves with automatic rollback on health regression |

## Phase 4 — intelligence

- Anomaly detection per device against its own baseline, not a fixed threshold
- Predictive maintenance signals (heap trend, reconnect frequency, RSSI drift)
- An MCP `explain_fleet_change` tool that diffs two windows and narrates what moved
- AEDi scheduled fleet digest into the Ionity Local Drive

## Decisions deliberately deferred

| Deferred | Revisit when |
|---|---|
| ESP-IDF rewrite | Power budget or secure boot becomes a requirement |
| Grafana instead of the custom dashboard | If metrics tooling matters more than the command/MCP surface |
| Per-device MCP endpoints | Not planned. It does not scale; `send_command` covers the need. |
| CoAP / LoRaWAN transports | Only if nodes go off-WiFi |

---

*Governance: Policy 986 AED · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd*
