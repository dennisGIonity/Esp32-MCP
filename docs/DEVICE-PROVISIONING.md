<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-005 | v1.0.0 | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# Provisioning 1000 devices

The whole design exists to make this boring. **One binary, no per-unit code
edits, no per-unit build.**

## The rule

| Property | Source | Changeable after flashing? |
|---|---|---|
| `device_id` | eFuse MAC → `esp32-aabbccddeeff` | No (and you don't want it to be) |
| `site` / `group` / `label` | NVS, defaults from `config.h` | **Yes** — `set_meta` over MQTT |
| WiFi credentials | `secrets.h`, compiled in | Reflash or OTA |
| Server / broker address | `config.h`, compiled in | Reflash or OTA |
| Telemetry interval, thresholds | Server-side (`.env`) | Yes, instantly, fleet-wide |

Nothing that varies per *unit* is compiled in. Only things that vary per
*deployment batch* are.

## Step 1 — build once

```powershell
cd E:\.ESP32-MCP\firmware
copy include\secrets.h.example include\secrets.h
# edit: WIFI_SSID, WIFI_PASSWORD, MQTT_USERNAME/PASSWORD, FLEET_TOKEN, OTA_PASSWORD
pio run -e esp32s3
```

Artifact: `.pio/build/esp32s3/firmware.bin`. This one file goes on every unit.

## Step 2 — flash the batch

Single unit:

```powershell
pio run -e esp32s3 -t upload -t monitor
```

Many units — flash directly, skipping the rebuild:

```powershell
# one board at a time, or several USB hubs in parallel
esptool.py --chip esp32s3 --port COM7 --baud 921600 write_flash `
  0x0 .pio\build\esp32s3\firmware.bin
```

For real volume, use a production jig: a powered USB hub, a loop over the COM
ports enumerated by `mode`, and 4–8 `esptool` processes in parallel. Roughly
20 s per board at 921600 baud, so ~1000 boards is a long day with 8 ports, not
a week.

## Step 3 — confirm enrolment

Each node self-registers on its first telemetry message. Nothing to do on the
server. Watch them arrive:

```powershell
curl http://192.168.0.3:8099/api/v1/fleet/summary
curl "http://192.168.0.3:8099/api/v1/devices?limit=20"
```

A freshly flashed board shows as `site=ionity-local`, `group=default`,
`label=<device_id>`.

## Step 4 — tag them in place

Instead of tracking which MAC went into which rack on paper, do it on
installation. Press `Identify` in the dashboard until the right LED blinks,
then tag:

```bash
curl -X POST http://192.168.0.3:8099/api/v1/devices/esp32-a1b2c3d4e5f6/cmd \
  -H "Content-Type: application/json" \
  -d '{"action":"set_meta","site":"kelvin-drive","group":"power","label":"GF riser"}'
```

The node writes NVS, reboots, and comes back publishing on the new topic.

Bulk-tag from a CSV of `mac,site,group,label` with a short loop against the
same endpoint — this is the intended workflow for a warehouse install.

## Step 5 — OTA from then on

First flash is over USB. Every update after that is over the network:

```powershell
pio run -e esp32s3_ota -t upload --upload-port 192.168.2.57
```

Find the IP from `GET /api/v1/devices/{id}`, or resolve the mDNS hostname
`ionity-esp32-<mac>.local`.

**Roll a fleet carefully.** Stage it: 1 device → 10 → 100 → the rest, checking
`fleet_summary` between waves. `firmware` version appears in the summary's
`firmware` breakdown, so you can watch the rollout complete.

## Pre-deployment checklist

- [ ] `secrets.h` filled in; confirm it is **not** tracked (`git status`)
- [ ] `FLEET_TOKEN` rotated from the default
- [ ] `allow_anonymous false` in `mosquitto.conf` + `passwd` created
- [ ] `IONITY_REQUIRE_TOKEN=true` in `.env`
- [ ] `IONITY_RETENTION_DAYS` set to what the disk can hold
- [ ] Alert thresholds tuned against a week of real data, not defaults
- [ ] Server starts on boot (Windows service / `restart: unless-stopped`)
- [ ] `data/fleet.db` included in the Ionity Local Drive backup set

## Troubleshooting

| Symptom | Check |
|---|---|
| Device never appears | Serial monitor at 115200. WiFi joined? Broker reachable? `ping 192.168.0.3` from the same LAN. |
| Appears then goes `offline` | Power supply. ESP32 WiFi TX draws ~300 mA peaks; a weak USB source browns it out. |
| `transport: http` when broker is up | Node failed 3 MQTT connects. Check credentials and `allow_anonymous`. |
| Everything `stale` at once | Server clock jumped, or the writer task is wedged — check `/api/v1/health` `queue_depth`. |
| `dropped_writes` climbing | Ingest exceeds SQLite throughput. Raise the interval or move to TimescaleDB. |
| Duplicate `device_id` | Two boards with cloned eFuse MACs (happens with grey-market modules). Override with an NVS `device_id`. |

---

*Governance: Policy 986 AED · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd*
