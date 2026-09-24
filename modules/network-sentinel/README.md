> **Merged into Esp32-MCP on 2026-09-25** from `github.com/dennisGIonity/Ionity-ESP32-Reporter` (commit `c9e59e9`), without that repo's history because its history contains plaintext credentials. It runs as its own service (`python run.py`, port 8000, `SENTINEL_PORT`) next to the fleet server (8099). Firmware: `firmware-arduino/Esp32_Network_Sentinel` (WiFi in git-ignored `secrets.h`). Passwords: set `MIKROTIK_PASSWORD` / `ESKOM_API_KEY` in `config/.env` (git-ignored), never in `config.yaml`.

# 🛰️ Kelvin Drive Edge Network Sentinel & AI MCP Gateway

An enterprise-grade, real-time edge telemetry system and Model Context Protocol (MCP) server designed for the **Kelvin Drive Router (MikroTik CRS326-24G-2S+)** network topology.

Deploys **alongside the router (Out-of-Band)** on a switch management/mirror port, providing zero-latency, non-intrusive monitoring of dual Liquid Fibre WAN uplinks, multi-floor office VLANs, PBX VoIP streams, power grid outages (Eskom Load Shedding), and blocked port intrusion attempts.

---

## 📐 Network Architecture Overview

```
+-----------------------------------------------------------------------------------+
|                                  WAN / ISP Uplinks                                |
|  [ISP 1: Liquid Fibre vlan11 (102.33.102.218/29)]   [ISP 2: Liquid Fibre ether9]  |
+--------------------------+-------------------------------------+------------------+
                           |                                     |
                           v                                     v
+-----------------------------------------------------------------------------------+
|                        MikroTik CRS326-24G-2S+ (Kelvin Drive)                     |
|  - ether1: Unused WAN                                                             |
|  - ether2 - ether5: 4Gbps LAN Bonding Group (Offices GF / FF / LG / Printers)     |
|  - ether9: Liquid Fibre Uplink 2 (41.169.150.194/29)                              |
|  - ether10: PBX Gateway (169.239.8.0/24 -> 192.168.243.250)                       |
|  - ether24 (or vlan200): Dedicated Sentinel Telemetry & Mirror Port               |
+----------------------------------------------------+------------------------------+
                                                     |
                 +-----------------------------------+-----------------------------------+
                 |                                                                       |
                 v (Port Mirror / NetFlow / SNMP / API)                                  v (VLAN 200 Mgmt)
+-----------------------------------------------+       +-----------------------------------------------+
|     Deployment Option A: Linux Edge Gateway   |  OR   |     Deployment Option B: ESP32-S3 Sentinel    |
|   (NanoPi R2S/R4S, CM4, or Dedicated Mini PC) |       |   (ESP32-S3-WROOM / CoreBoard + W5500 / WiFi) |
|   - Real-time packet parsing & NetFlow sink   |       |   - Out-of-band ICMP jitter & loss prober     |
|   - Active & passive Speedtest engine         |       |   - AC Mains Power & Loadshedding optocoupler |
|   - Full Model Context Protocol (MCP) server  |       |   - MQTT / REST telemetry transmitter         |
|   - Real-time Glassmorphic Web Dashboard      |       |   - Onboard OLED status display               |
+-----------------------------------------------+       +-----------------------------------------------+
```

---

## 🌟 Key Features

1. **Out-of-Band Zero-Bottleneck Architecture**:
   - Ingests telemetry via RouterOS REST API, SNMP v2c/v3, NetFlow (IPFIX), and Port Mirroring.
   - Traffic flows through the hardware switch ASIC of the CRS326 at full wire speed (no SPI/microcontroller chokepoints).
2. **Link Stability Index (0–100%)**:
   - Continuous sub-second ICMP & socket latency probes to Google DNS (`8.8.8.8`), Cloudflare (`1.1.1.1`), and Liquid ISP gateways (`102.33.102.217` & `41.169.150.193`).
   - Calculates rolling jitter (ms), packet loss %, and status rating (`EXCELLENT`, `STABLE`, `DEGRADED`, `CRITICAL`).
3. **Eskom Load Shedding & Grid Correlation Engine**:
   - Integrates with the **EskomSePush API** for Sandton / Kelvin Drive Substation (`city-of-johannesburg-block-3`).
   - **Outage Root-Cause Disambiguation**:
     * *Grid Power Loss*: Load shedding active in Sandton or building mains drop.
     * *Physical Fibre Cut*: Mains power normal + no load shedding, but Liquid Fibre uplink down.
4. **Security & Blocked Services Radar**:
   - Inspects firewall drops for attempts to access blocked services (`Telnet 23`, `FTP 21`, `WWW 80`, `SSH 22`, `MikroTik API 8728`).
   - Real-time IP flagging and threat intelligence ranking.
5. **Model Context Protocol (MCP) Server**:
   - JSON-RPC 2.0 interface for AI models (AEDi, Gemini, Antigravity, Claude) to query live network health.
6. **Real-time Glassmorphic Dashboard**:
   - Live speed gauge, dynamic bandwidth Chart.js plots, floor-by-floor VLAN usage, and interactive MCP console.

---

## 🚀 Quick Start Guide

### 1. Run with Python
```bash
# Install dependencies
pip install -r requirements.txt

# Start the Sentinel daemon and Web Dashboard
python run.py
```
Open **[http://localhost:8000/](http://localhost:8000/)** in your browser.

### 2. Run with Docker Compose
```bash
docker-compose up -d
```

---

## 🤖 Model Context Protocol (MCP) Integration

The Sentinel exposes standard MCP endpoints over stdio and HTTP JSON-RPC (`/api/mcp/rpc`).

### Available MCP Tools:

| Tool Name | Parameters | Description |
|---|---|---|
| `get_link_stability` | `detailed: boolean` | Returns stability score, jitter, packet loss %, and WAN failover state. |
| `get_traffic_feed` | `floor_id: string` | Returns real-time ingress/egress Mbps for WANs, PBX, and office floors. |
| `run_speedtest` | `force_fresh_test: boolean` | Executes active bandwidth speedtest with speed-bar level. |
| `get_loadshedding_status`| *none* | Returns Eskom stage, area timetable, and outage correlation analysis. |
| `get_security_alerts` | `limit: integer` | Lists flagged IPs probing blocked ports (Telnet, FTP, SSH, API). |
| `get_network_datasheet` | *none* | Complete hardware datasheet and VLAN specification for Kelvin Drive. |

---

## 🔧 MikroTik CRS326 Provisioning Commands

Paste the following into your MikroTik Terminal:

```routeros
# Read-Only Telemetry User
/user group add name=sentinel_group policy=read,api,test,!write,!policy,!password
/user add name=sentinel_readonly group=sentinel_group password="CHANGE_ME_in_env" address=10.53.20.0/24 comment="Edge Sentinel Telemetry Service"

# SNMP v2c on Management VLAN 200
/snmp community add name=public addresses=10.53.20.0/24 read-access=yes
/snmp set enabled=yes contact="Ops Team (devlin@grayaura.co.za)" location="Kelvin Drive Server Room"

# Firewall Drops for Blocked Services
/ip firewall filter add chain=input protocol=tcp dst-port=21,23,80,22,8728 action=drop log=yes log-prefix="BLOCKED_SVC_PROBE: " comment="Diagram Blocked Services Filter"
```

---

## ⚡ ESP32-S3 Hardware Sentinel (Optional Micro-Probe)

For out-of-band hardware telemetry and mains AC power sensing:
1. Open [`hardware/esp32_sentinel/esp32_sentinel.ino`](file:///e:/.RouterProject/hardware/esp32_sentinel/esp32_sentinel.ino) in Arduino IDE or VS Code PlatformIO.
2. Configure your WiFi or SPI W5500 settings in [`hardware/esp32_sentinel/config.h`](file:///e:/.RouterProject/hardware/esp32_sentinel/config.h).
3. Flash to your **ESP32-S3 CoreBoard**.
4. The ESP32-S3 will autonomously measure jitter and mains AC status and stream telemetry to `POST /api/telemetry/hardware-feed`.
