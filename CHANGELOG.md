<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-CHG | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# Changelog

## 2.0.1 — 2026-10-02 (A-to-Z audit; see `docs/AUDIT-2026-10-02.md`)

### Fixed
- **network-sentinel did not import** (`NameError: Optional`): the :8000 service could not start and
  its CI job had been red since the merge. One-line fix + an import-smoke test over every module.
- **Offline-buffer replay lost its timestamps:** firmware now sends `age_ms` on every reading and the
  host backdates against its own clock (device `ts` is clamped to ±10 min). 17 same-second bursts
  were visible in the lab database.
- `dns_probe` reply parser is bounds-checked on every read and runs as a job from `loop()` (one name
  per pass) instead of blocking the MQTT callback for up to 18 s.
- LAN DNS resolver: answers only allow-listed client networks (`IONITY_DNS_ALLOW_FROM`, RFC1918 +
  loopback by default), keeps task references with a 512 in-flight cap, evicts a full cache, and
  only accepts upstream replies that match its transaction id.
- Sentinel speed test: real `speedtest-cli` off the event loop; simulation only when configured and
  every result carries `source` / `simulated` (dashboard shows a SIMULATED badge). `POST
  /api/telemetry/speedtest/run` and `/hardware-feed` take `SENTINEL_ADMIN_TOKEN` / `SENTINEL_FEED_TOKEN`.
- Sentinel firmware 2.5.1: NTP configured on every WiFi (re)join, probes alternate (one blocking
  connect per tick), ArduinoJson payload, optional `X-Fleet-Token`.
- Pi agent 1.0.1: commands run off paho's network thread; the Will is re-armed after `set_meta`.
- Datadog: service checks posted as one array per flush instead of one request per board.
- CORS: `allow_credentials` off (Bearer header, no cookies); `IONITY_CORS_ORIGINS` documented.
- Dashboard: keyed in-place card updates (focus survives the 2 s tick), native `<dialog>` drawer,
  reconnect back-off with jitter, DNS rcode names.
- On-device MCP HTTP: 6 KB body cap, constant-time token compare, `/info` honours the token.
- Small: strapping pins 45/46 removed from the S3 OLED scan pool; agent-set `led` level survives the
  heartbeat pulse; port range check in `provSet`; `.env` BOM removed; serial bridge logs "no ports"
  once then hourly; `FleetRegistry._seq` per instance.

### Changed
- Writer drains a batch with two `executemany` round trips (devices, telemetry) instead of three
  awaits per reading; `telemetry_metric(ts)` index so the hourly prune no longer scans the table;
  `busy_timeout` / `temp_store` pragmas.
- `server/requirements.txt` pinned; `paho-mqtt` listed explicitly (unused `aiomqtt` dropped).
- CI: ruff (undefined names, bugbear, async, security) + `pip-audit` + `npm audit` gates.
- Docker: multi-stage image with the flasher built in, non-root user, `HEALTHCHECK`; compose waits
  on broker health, mounts `firmware/dist`, runs the resolver unprivileged on 5353→53/udp.
- Broker: `infra/mosquitto/config/acl` (server = only command publisher; boards confined to their
  own topics by client id). **Still anonymous** until the lab boards carry the `ionity_fleet`
  login — steps in `mosquitto.conf`.

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
