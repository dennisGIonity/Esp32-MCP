# Ionity fleet agent — Raspberry Pi Zero / Zero 2 W / any Linux board

Doc ID: DOC-2026-09-ESP32MCP-PIAGENT · Policy 986 AED · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

Makes a Raspberry Pi report into the Ionity fleet **exactly like an ESP32 node**: same MQTT
topics, same JSON, same commands. The fleet server, dashboard and MCP tools need no changes.
Works on Pi Zero W, Zero 2 W, 3/4/5 and any systemd Linux box with Python 3.9+.

## Install (on the Pi, ~1 minute)
```bash
# copy this folder to the Pi (or git clone the repo), then:
cd pi-agent
sudo bash install.sh --site lab --group bench --label "pi-zero-01"
```
It installs `python3-paho-mqtt` and mDNS support if they're missing, then:
- copies the agent to `/opt/ionity-agent/`, the config to `/etc/ionity-agent.conf` (existing config kept),
- installs `ionity-agent.service` (runs at boot, restarts on failure, 64 MB memory cap),
- prints one live reading so you can see it works.

Logs: `journalctl -u ionity-agent -f` · Remove: `sudo bash install.sh --uninstall`

## What it reports (every 10 s)
| Metric | Source |
|---|---|
| `temp_c` | SoC temperature (`/sys/class/thermal`) |
| `rssi_dbm` | WiFi signal (`/proc/net/wireless`) |
| `free_heap_bytes` | available RAM (same name as the ESP32 metric, so fleet-wide queries work) |
| `mem_used_pct`, `cpu_pct`, `load_1m`, `disk_free_pct` | Linux basics |
| `throttled` | `vcgencmd get_throttled` bit field: 0 = healthy; non-zero = under-voltage or heat throttling |

Identity comes from the silicon: `pi-<cpu serial>` (e.g. `pi-a1b2c3d4`), so nothing per unit is configured.
`product` is the board model, e.g. *Raspberry Pi Zero 2 W Rev 1.0*.

## Finding the server
Same order as the ESP32 firmware: `server =` in the config → mDNS `ionity-fleet.local` → `server_fallback`
(the lab's `192.168.124.4`). The MQTT broker is on the same host (`:1883`). Without a broker, or without
paho-mqtt, telemetry goes over HTTP (`:8099/api/v1/telemetry`), but commands need MQTT.

## Commands (from the dashboard or Claude via MCP `send_command`)
| Action | On a Pi |
|---|---|
| `ping` | replies `pong` |
| `identify` | blinks the green ACT LED, then hands it back to the SD-card activity trigger |
| `set_meta` | re-tags site / group / label **without a reboot** (saved in `/var/lib/ionity-agent/state.json`) |
| `reboot` | `systemctl reboot` after replying |
| `dns_probe` | raw A-queries to one resolver (default `gf_dns`, Gate^Flame); same answer format as the ESP32 |
| `set_display` | replies "no display" |

## Try it without installing
```bash
python3 ionity_agent.py --config ionity-agent.conf.example --once   # prints one reading
```
