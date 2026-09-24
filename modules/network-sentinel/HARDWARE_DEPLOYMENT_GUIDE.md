# Hardware Deployment Guide: Kelvin Drive Network Sentinel (Alongside Router)

This guide documents the physical deployment, MikroTik CRS326-24G-2S+ configuration, and hardware integration for the **Edge Sentinel** telemetry system.

---

## 1. Physical Architecture & Topology

The Sentinel is deployed **alongside** the router (Out-of-Band) to prevent any inline bottlenecking of the Gigabit Liquid Fibre uplinks.

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

## 2. MikroTik CRS326 RouterOS Configuration Script

Run the following commands in the MikroTik Terminal (`WinBox` or `SSH`) to enable read-only telemetry, traffic-flow, and port mirroring:

```routeros
# 1. Create a secure Read-Only user for the Sentinel Collector
/user group add name=sentinel_group policy=read,api,test,!write,!policy,!password
/user add name=sentinel_readonly group=sentinel_group password="CHANGE_ME_in_env" address=10.53.20.0/24 comment="Edge Sentinel Telemetry Service"

# 2. Enable SNMP v2c on Management VLAN 200
/snmp community add name=public addresses=10.53.20.0/24 read-access=yes
/snmp set enabled=yes contact="Ops Team (devlin@grayaura.co.za)" location="Kelvin Drive Server Room"

# 3. Configure Traffic Flow (NetFlow / IPFIX) exporter to Sentinel IP (10.53.20.150)
/ip traffic-flow set enabled=yes interfaces=all active-flow-timeout=1m inactive-flow-timeout=15s
/ip traffic-flow target add dst-address=10.53.20.150 port=2055 version=ipfix

# 4. Optional: Switch Port Mirroring (Mirror ether9 Liquid WAN to ether24 for deep packet inspection)
/interface ethernet switch set mirror-source=ether9 mirror-target=ether24

# 5. Firewall Filter logging for Blocked Services (Telnet, FTP, WWW, SSH, API)
/ip firewall filter add chain=input protocol=tcp dst-port=21,23,80,22,8728 action=drop log=yes log-prefix="BLOCKED_SVC_PROBE: " comment="Diagram Blocked Services Filter"
/ip firewall filter add chain=forward protocol=tcp dst-port=21,23,80,22,8728 action=drop log=yes log-prefix="BLOCKED_FWD_PROBE: " comment="Diagram Blocked Services Filter"
```

---

## 3. ESP32-S3 Coreboard Pinout & Hardware Schematic

When deploying the **ESP32-S3 CoreBoard** as a hardware sentinel:

| ESP32-S3 GPIO | Function | Description |
|---|---|---|
| **GPIO 10** | `SPI_CS` | W5500 Ethernet Controller Chip Select |
| **GPIO 11** | `SPI_MOSI` | W5500 Ethernet Data In |
| **GPIO 12** | `SPI_MISO` | W5500 Ethernet Data Out |
| **GPIO 13** | `SPI_SCK` | W5500 Ethernet Clock |
| **GPIO 14** | `W5500_INT` | Ethernet Interrupt Pin |
| **GPIO 4** | `MAINS_SENSE` | Optocoupled 230V AC Mains Detection (HIGH = Mains OK, LOW = Load Shedding) |
| **GPIO 5** | `I2C_SDA` | 0.96" OLED Display (SSD1306) Data |
| **GPIO 6** | `I2C_SCL` | 0.96" OLED Display (SSD1306) Clock |
| **GPIO 7** | `LED_STATUS_BLUE` | Blinks on active heartbeat & telemetry TX |
| **GPIO 8** | `LED_ALERT_RED` | Lights up on WAN link down or Eskom power cut |
| **GPIO 9** | `BUZZER / RELAY` | Audio alarm or secondary failover trigger relay |
