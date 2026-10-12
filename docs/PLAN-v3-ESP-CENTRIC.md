# ESP32-MCP v3 — ESP-centric recovery plan

Doc ID: DOC-2026-10-ESP32MCP-PLANV3 | Version 1.0 | 2026-10-11 SAST
Policy 986 AED | License AED 900 | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved - TM2
Web: https://www.ionity.today | Ref: https://www.ionity.co.za
Source spec: MASTER PROMPT "Fleet Platform Recovery & Correct Architecture (v2.1+)", Oct 2026

## 1. Understanding of the corrected requirements

Every observation starts on the ESP32 and flows outward. Each board is a
self-contained network observer and micro-AI node with its own micro-MCP
server. The fleet host only aggregates, stores, alerts and proxies tool calls
down to boards. The host never scans networks. The dashboard's "Devices on
this network" list belongs to one ESP and is fed by that ESP's
`get_local_devices` tool. The design must hold from 1 000 to 100 000+ nodes,
bandwidth is precious, and only metadata is collected (never payloads), on
networks the operator controls and has consent for.

## 2. The physical constraint (why phones showed nothing)

An ESP32 joined to Wi-Fi as a normal station cannot read other devices' DNS or
traffic. WPA2/WPA3 encrypts each client with its own pairwise key, and the
access point delivers unicast frames only to their owner. Promiscuous mode
captures those frames but only their 802.11 headers are readable. No firmware
change can get around this; the 2.2.0 design worked only because the router
sent everyone's DNS to the laptop.

What an ESP in pure station mode CAN observe:

| Signal | How | Gives |
|---|---|---|
| DNS (domains, client IP, qtype, rcode) | ESP is the LAN DNS resolver (router DHCP DNS = ESP IP) | Full per-device DNS log, flags in real time |
| Local devices | DNS clients + ARP resolve of their IPs + mDNS/SSDP/DHCP broadcasts it receives | MAC, IP, hostname, vendor OUI, last seen |
| Per-device airtime/volume | Promiscuous mode, header-only, same channel, filtered to own BSSID | Frames and bytes per MAC, RSSI; no IPs, no payload |
| Upstream health | Probes it originates | Latency, loss, jitter |

What needs extra infrastructure: true flow stats (5-tuples, ports, protocol
mix) need a wired ESP32 on a switch mirror/SPAN port (classic ESP32 + LAN8720,
or S3 + W5500), or flow export from the router.

Decision (2026-10-11): ESP-as-DNS-resolver is the default observation path for
every site; the firmware is structured so a wired mirror-port variant plugs in
later behind the same tools. The one site setting (router DHCP DNS = ESP IP)
is documented in the install guide and checked by the board itself
(`dns_role: active | idle`).

Known limit, stated plainly: phones using Private DNS / DNS-over-HTTPS bypass
any LAN resolver. The ESP reports such devices as "seen, DNS silent".

## 3. Audit — current repo vs spec

| Area | Today (v2.2.0) | Spec v3 | Action |
|---|---|---|---|
| Firmware tools | get_device_info, read_telemetry, run_inference, set_actuator, set_state_mode, identify | + get_dns_activity, query_dns_log, get_network_stats, get_local_devices, set/get_flag_rules | Add NetObserve module |
| DNS capture | Host `services/fleet-host/server/app/ingest/dns_resolver.py` (laptop is the resolver) | On the ESP | Port resolver + flag logic to firmware; host resolver becomes optional lab fallback, off by default |
| LAN device list | Host LAN sweep (`/api/v1/network/overview`, `list_lan_devices`) | Per-ESP `get_local_devices` | Retire host sweep from defaults and dashboard |
| Flag rules | Host watch list (YouTube/TikTok) | Rules pushed to ESPs, evaluated on-device | New `manage_flag_rules` fleet tool + NVS-stored rules |
| Network-sentinel module | Separate host-side probe stack + separate sentinel firmware | Absorb probes into node firmware | Fold latency/loss probes into `get_network_stats`; retire module |
| Dashboard | Global "Every device on this network" panel | Per-device dropdown + DNS feed + traffic + rules UI | Rework |
| Emulator / simulator | Telemetry only | Must emit DNS/stats/local-devices | Extend |
| Flasher | Provision + verify tools/list | Verify the new tools are present | Extend verify step |

## 4. Firmware design (Esp32_MCP_Node, single S3 image)

New module `NetObserve.ino`:

- **DNS resolver task**: UDP :53 forwarder (upstream = DHCP-learned gateway DNS, fallback 1.1.1.1).
  Each query is logged to a 512-entry PSRAM ring (ts, client IP, qname, qtype, rcode) and
  aggregated into per-client and per-domain counters. A cache of 256 answers keeps
  forwarding cheap. Design target (to be measured): 150+ queries/s on S3 with < 40 KB heap.
- **Flag engine**: rules are domain suffix, IP/CIDR, per-client query-rate threshold and anomaly
  score. They are stored in NVS and evaluated inline; a match raises a priority event immediately.
- **Local-device table**: 128 entries keyed by MAC, fed by DNS clients (IP→MAC via lwIP ARP),
  received mDNS/SSDP/DHCP broadcasts and promiscuous header stats. OUI vendor prefix table in flash.
- **Promiscuous header sampler** (optional, on by default when channel is stable): data-frame
  headers only, filtered to own BSSID; per-MAC frames, bytes, RSSI.
- **Reporting**: one compact batch every 30 s (CBOR over MQTT, JSON fallback) with
  counters + top-N; flagged events sent immediately; offline ring buffer on disconnect.

New micro-MCP tools: `get_dns_activity`, `query_dns_log`, `get_network_stats`,
`get_local_devices`, `set_flag_rules`, `get_flag_rules`. `read_telemetry` gains
`net_*` fields. Existing tools are unchanged.

## 5. Fleet host

New storage tables `esp_dns_events`, `esp_net_stats`, `esp_local_devices`,
`flag_rules`. New MCP tools: `query_dns_activity`, `get_network_stats_fleet`,
`list_local_devices_on_esp`, `manage_flag_rules`. `device_call_tool` unchanged.
The host DNS resolver and LAN sweep are disabled by default (`IONITY_HOST_DNS=0`,
`IONITY_HOST_SWEEP=0`) and documented as lab-only fallbacks. Datadog: new
metrics `ionity.net.dns_qps`, `ionity.net.flagged`, `ionity.net.local_devices`.

Scale path: SQLite for the lab and up to ~1k nodes; partitioned
Postgres/Timescale or ClickHouse plus a clustered MQTT broker (EMQX) for
10k–100k+. Boards send aggregates rather than raw logs, so host load stays
proportional to the number of nodes, not to the number of queries.

## 6. Dashboard

Per-device panel with an expandable "Devices on this network" list (from that
ESP), a live DNS feed filterable by device, site and flagged-only, a traffic
overview per ESP, and a flag-rules editor that pushes rules to one ESP, a
group or the whole fleet. The global network panel is removed.

## 7. Delivery order

1. Firmware NetObserve + six tools, emulator support, unit tests (host-side parsers).
2. Host ingest, storage, MCP tools, retire host sweep from defaults.
3. Dashboard rework.
4. Flasher verify step + SET-BOARD-WIFI follow-on "SET-ROUTER-DNS" guide.
5. Docs (ARCHITECTURE, DEVICE-MCP, FLASHER, scale guide, privacy/consent note), CHANGELOG, v3.0.0.
6. Live test on Afrihost Fibre DTM: router DNS → ESP, phone opens YouTube, board flags it and red light fires.

## 8. Migration from v2.2.0

Flash v3 firmware (NVS provisioning survives). On the router, point DHCP DNS at
the ESP's reserved IP instead of the laptop (192.168.0.3). Start the host with
the defaults; the host resolver and sweep stay off. Existing telemetry, alerts
and device history remain in the database.
