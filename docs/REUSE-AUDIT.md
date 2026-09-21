<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-002 | v1.0.0 | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# RouterProject reuse audit

`E:\.RouterProject` was imported verbatim to `_reference/router-project/`
(minus `.git` and `__pycache__`). This is the line-by-line verdict on what
carried over.

The one-sentence summary: **RouterProject is an excellent single-site network
sentinel. Its shape is right and its MCP layer is reusable; everything that
assumed exactly one device had to be rebuilt.**

---

## Adopted — kept, largely intact

| From RouterProject | Where it landed | Note |
|---|---|---|
| `mcp/mcp_server.py` JSON-RPC dispatcher | `server/app/mcp/server.py` | Same `initialize` / `tools/list` / `tools/call` / `resources/*` shape. Added `ping`, `notifications/initialized`, batch requests and a stdio transport. |
| `mcp/tools_and_resources.py` split (definitions separate from execution) | `server/app/mcp/tools.py` | Good pattern — kept. Tool set replaced (fleet-wide, not one router). |
| `api/main.py` lifespan + engines on `app.state` | `server/app/main.py` | Same idea. Engines are now store / registry / mqtt / mcp. |
| WebSocket broadcaster loop | `server/app/main.py:broadcaster` | Kept, but the frame is now capped and pre-aggregated so 1000 devices don't push megabytes per tick. |
| Glassmorphic dark dashboard aesthetic | `dashboard/style.css` | Visual language carried over, restyled to AEDI tokens. |
| `hardware/esp32_sentinel/config.h` structure | `firmware/include/config.h` | Same "one header of defines" ergonomics. Split per-unit secrets into `secrets.h`. |
| ICMP probe + stability scoring | `firmware/src/main.cpp:runProbe()` | Trimmed to 3 targets, one of which is auto-detected gateway. Now optional (`PROBE_ENABLED`). |
| Alert-on-threshold idea (stability < 70% → LED) | `server/app/fleet/registry.py:_evaluate_alerts` | Moved server-side so thresholds change without reflashing 1000 devices. |
| `docker-compose.yml` + `Dockerfile` | `docker-compose.yml`, `infra/Dockerfile` | Rewritten, but the deploy story is the same. |

---

## Rebuilt — right idea, wrong scale

| Problem in RouterProject | Why it breaks at 1000 devices | What replaced it |
|---|---|---|
| `esp32_sentinel.ino` hardcodes `SENTINEL_DEVICE_ID`, IP, site | Editing a header per unit means 1000 builds | `device_id` derived from eFuse MAC; site/group/label in NVS, settable over MQTT `set_meta`. One binary. |
| Telemetry via `HTTPClient.POST` every 3 s | 1000 × 3 s = 333 TCP+TLS handshakes/sec | MQTT persistent connection primary; HTTP kept only as fallback after 3 failed broker connects. |
| `POST /api/telemetry/hardware-feed` writes straight through | Per-message DB commit does not survive 100 msg/s | Registry queues; one writer task batches 200 rows per commit. |
| Fixed `ProbeMetrics` struct → fixed JSON keys | Adding a sensor needs a firmware *and* server change | Open `metrics{}` map; numeric values auto-land in `telemetry_metric` and become MCP-queryable with zero server change. |
| No device registry — server assumes "the" sentinel | No concept of online/stale/offline, no inventory | `FleetRegistry` with per-device hot state, health windows and LWT handling. |
| No store-and-forward on the device | A 30 s WiFi blip loses data | 40-slot RAM ring buffer, flushed on reconnect. |
| No command path (server → device) | Can't reboot or re-tag a unit remotely | MQTT `cmd` topic + `cmd/result`, exposed as the `send_command` MCP tool and `POST /devices/{id}/cmd`. |
| Dashboard renders one device | Unusable for 1000 | Virtual-ish grid sorted by priority, filters, drill-down drawer, truncation notice. |
| No retention policy | SQLite grows unbounded at 100 msg/s | Hourly pruner, `IONITY_RETENTION_DAYS`. |

---

## Dropped — site-specific, not portable

These are excellent for Kelvin Drive and irrelevant here. They stay in
`_reference/` and can be lifted back if a site needs them.

| Dropped | Why |
|---|---|
| `core/mikrotik_collector.py` | RouterOS REST/SNMP against a CRS326. Fleet nodes aren't routers. Re-add as an optional per-site collector if you deploy at Kelvin Drive. |
| `core/loadshedding.py` (EskomSePush) | Genuinely useful for SA deployments — **candidate for re-adoption** as a server-side enrichment that stamps every reading with grid state. Logged in `ROADMAP.md`. |
| `core/speedtest_engine.py` | `speedtest-cli` on the server measures the server's link, not the fleet's. Per-node latency/loss already covers the fleet case. |
| `core/security_analyzer.py` | Parses MikroTik firewall drops. No equivalent source on an ESP32. |
| `core/traffic_analyzer.py` | Fan-in aggregator over the above collectors. Superseded by `FleetRegistry`. |
| Hardcoded Kelvin Drive topology, VLANs, ISP gateways | Site constants baked into code. Anything site-specific now lives in `site`/`group` metadata. |
| `SentinelSecurePassword2026!`, `SecureOpsPassword2026!` in tracked files | Credentials in git. All secrets moved to `.env` / `secrets.h`, both git-ignored. **Rotate these on the Kelvin Drive router — they are in that repo's history.** |

---

## Security carry-forward

Three things to fix before this leaves the lab, inherited as lessons from the audit:

1. **Credentials in source.** RouterProject ships real-looking passwords in
   `config.h` and the README. Here: `secrets.h` and `.env` are git-ignored and
   only `.example` files are tracked.
2. **Anonymous MQTT.** `mosquitto.conf` ships `allow_anonymous true` for the
   bench. Flip it and create a `passwd` file before any device leaves the desk.
3. **Unauthenticated ingest.** `IONITY_REQUIRE_TOKEN=false` by default so the
   simulator works out of the box. Turn it on once devices carry `FLEET_TOKEN`.

---

*Governance: Policy 986 AED · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd*
