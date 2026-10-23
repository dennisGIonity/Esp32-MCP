# Ionity fleet agent 2 — Raspberry Pi / any Linux board

Doc ID: DOC-2026-10-ESP32MCP-PIAGENT2 · Policy 986 AED · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

A Raspberry Pi joins the fleet **as a first-class node**: it speaks **EWP/1**, the same binary contract
as ESP32 firmware 3 (`packages/protocol`). The fleet host decodes Pi frames and ESP32 frames with the
same code. Both node types have the same device model, the same DNS pipeline and the same tools. Works on Pi Zero W,
Zero 2 W, 3/4/5 and any systemd Linux box with Python 3.9+ (standard library; `python3-paho-mqtt` for MQTT).

| File | What |
|---|---|
| `ionity_agent.py` | the node: HELLO / STATUS / TELEMETRY / EVENT, tool calls, MQTT + HTTP fallback |
| `netobserve.py` | the LAN DNS forwarder (UDP + TCP 53), watchlist, DHCP/mDNS/ARP discovery |
| `ewp_cbor.py` | EWP/1 frame + CBOR codec (stdlib only) |
| `ewp_protocol.py` | **generated** from `packages/protocol` (`npm run gen`) — never edit by hand |

## Install (on the Pi, ~1 minute)

```bash
cd pi-agent
sudo bash install.sh --site lab --group bench --label "pi-zero-01"
sudo bash install.sh --dns-forward                 # also make the Pi the LAN's DNS forwarder
sudo bash install.sh --dns-forward --dns-upstream 1.1.1.1,9.9.9.9
```

Copies the four files to `/opt/ionity-agent/`, keeps an existing `/etc/ionity-agent.conf` (configs from agent
1.x get the NetObserve keys appended), installs `ionity-agent.service` and prints the HELLO plus one reading.
Logs: `journalctl -u ionity-agent -f` · Remove: `sudo bash install.sh --uninstall`

## Topics (EWP/1)

| Topic | Frame | |
|---|---|---|
| `ionity/ewp1/<site>/<hwid>/hello` | HELLO | retained: fw, tools, actuators, metric names, `net` (DNS mode, upstreams, watchlist version) |
| `…/st` | STATUS | retained `ONLINE`; the Last Will is `OFFLINE` |
| `…/tm` | TELEMETRY | every `interval_s`: heap (MemAvailable), RSSI, CPU, SoC temperature, `ext` metrics, `dns` counters |
| `…/ev` | EVENT | `DNS_BATCH` every `dns_flush_s`; `DNS_FLAGGED` immediately on a watchlist hit; `LEASE_EXPIRED` |
| `…/dn` (+ `ionity/ewp1/_all/dn`) | REQ / CANCEL | tool calls from the host |
| `…/rs` | ACK / RES / ERR | replies |

`hwid` = `pi-<CPU serial>` (leading zeros dropped, e.g. `pi-a1b2c3d4`), else `pi-<MAC>`. The node UUID and boot
count live in `/var/lib/ionity-agent/state.json`. If no broker can be reached, the same frames are POSTed to
`http://<server>:8099/api/v1/ewp/<hwid>/<channel>` with `X-Fleet-Token`. Tool calls need MQTT.

## Tools

| Tool | On a Pi |
|---|---|
| `get_device_info`, `read_telemetry` | HELLO / TELEMETRY bodies |
| `identify` | blinks the ACT LED, then hands it back to its kernel trigger |
| `set_actuator` | channel 0 `alert_led` on/off, optional `leaseMs` (reverts and reports `LEASE_EXPIRED`) |
| `reboot` | replies, then `systemctl reboot` after `delayMs` |
| `set_watchlist`, `get_watchlist` | the domain watchlist (persisted, survives restarts) |
| `get_network_stats`, `get_local_devices`, `query_dns_log` | forwarder counters per upstream, LAN client table, recent queries |
| `scan_lan`, `flush_dns_cache` | ARP sweep of the /24 (no root needed), empty the answer cache |

The DNS tools appear only with `dns_mode = forward`.

## The Pi as LAN DNS forwarder

1. `sudo bash install.sh --dns-forward`. The agent listens on UDP **and** TCP 53. If a local stub resolver holds `0.0.0.0:53`, it binds the LAN address instead.
2. On the router: **LAN → DHCP → DNS server = the Pi's address**, with no secondary. Then renew leases.
3. Every query is answered from the cache or forwarded to `dns_upstream`. The upstreams are always explicit addresses; the agent never forwards to the router, because the router would hand the query back to the Pi. For each query the agent records the client IP, the MAC (from `/proc/net/arp`), the host name (DHCP option 12 / mDNS), the QNAME, the QTYPE, the RCODE and the latency. It never records the answers or any page content.
4. **Watchlist**: YouTube (`youtube.com`, `*.googlevideo.com`, `*.ytimg.com`, `youtu.be`, `youtubei.googleapis.com`, …)
   and TikTok are built in (from `packages/protocol/src/watchlist.ts`). The host replaces the list with its own rule set.
   An ALERT match publishes `DNS_FLAGGED` at once, no more than once per client and rule every 30 s, and lights `alert_led` for `alert_hold_s`.
5. Only clients on private, link-local or loopback networks get answers, so the Pi is never an open resolver.

Not visible: devices using DNS-over-HTTPS / DoT (Android "Private DNS", iCloud Private Relay, browser
"secure DNS"). Block outbound TCP/UDP 853 on the router to push Android back to the LAN resolver.
Only monitor networks you manage, with the users' knowledge (POPIA).

## Try it without installing

```bash
python3 ionity_agent.py --config ionity-agent.conf.example --once      # HELLO + TELEMETRY, decoded
cd devices/pi-agent && python3 -m pytest -q                              # 14 tests, real sockets on loopback
```
