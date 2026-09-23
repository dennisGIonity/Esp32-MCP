# Ionity Lab Template — isolated IoT lab next to household internet

Doc ID: DOC-2026-09-LAB-TEMPLATE · Policy 986 AED · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
Use this for **every** hardware project: the lab never touches the household internet.

## The layout

```
 Afrihost fibre
      │
 TP-Link EX511  192.168.0.1   ← HOUSEHOLD. Internet only. Owns 2.4 GHz.
  2.4 GHz ch 1 / 20 MHz / High power (phones) · 5 GHz ch 36 (laptop, S10e)
      │ WiFi (metric 10 = all internet + DNS)
 ┌────┴─────────────── LAPTOP ───────────────┐
 │ fleet server :8099 · MQTT :1883 · mDNS    │
 │ DNS logger :53 (bound to lab IP only)     │
 └────┬──────────────────────────────────────┘
      │ Ethernet → H3C **LAN** port (metric 200 = lab only, never internet)
 H3C Magic  192.168.124.1                      ← LAB. Isolated.
  IONITY-LAB      5 GHz ch 149  ← main lab network (Pi 5, laptops, demo phones, future ESP32-C5)
  IONITY-LAB-IOT  2.4 GHz ch 11 / 20 MHz / LOW power ← only for 2.4-only boards on the bench
      │
 ESP32 boards · Pico W · Pi 5 (GateFlame, paused)   all on 192.168.124.x
 laptop pinned at 192.168.124.4 (DHCP reservation on the H3C)
```

Single source of truth: [`config/lab.json`](../config/lab.json) (no secrets in it).

## Rules
1. **Nothing lab-related on the household network**: no boards, no lab DNS, no GateFlame.
2. Laptop cable goes into an H3C **LAN** port. Never the WAN port.
3. Band split: the **household owns 2.4 GHz** (TP-Link), the **lab lives on 5 GHz** (H3C `IONITY-LAB`, ch 149).
4. Exception: ESP32-S3 / Pico 2 W radios are **2.4 GHz only**. The lab keeps one low-power 2.4 GHz network
   (`IONITY-LAB-IOT`, ch 11) just for them, on a channel that never overlaps the household's ch 1.
   Dual-band boards (ESP32-C5) join the 5 GHz network instead.
5. WiFi passwords live only in git-ignored `secrets.h`. They're typed into `SET-LAB-WIFI.cmd`, never in chat or commits.

## Setting up a new lab (or rebuilding this one)

| # | Step | Who |
|---|------|-----|
| 1 | Laptop cable → H3C LAN port | you |
| 2 | H3C admin (http://192.168.124.1): 5 GHz SSID `IONITY-LAB` ch 149; 2.4 GHz SSID `IONITY-LAB-IOT` ch 11, 20 MHz, low power (turn band steering / "merge 2.4+5" **off** so they stay separate); reserve **192.168.124.4** for the laptop NIC (MAC `40:C2:BA:F5:B9:5F`) | you (router password) |
| 2b | TP-Link (http://192.168.0.1): 2.4 GHz ch 1, 20 MHz, power High; 5 GHz ch 36 | you |
| 3 | Double-click **`SETUP-LAB-NETWORK.cmd`** → approve UAC. Sets metrics, marks the lab network Private, opens lab ports to `192.168.124.0/24` only | you (admin) |
| 4 | Double-click **`SET-LAB-WIFI.cmd`** → accept `IONITY-LAB-IOT`, type its password (hidden) | you |
| 5 | `scripts\start_lab.ps1 -Restart` | Claude |
| 6 | Each board on USB: `scripts\add_device.ps1 -Port COMx` | Claude |
| 7 | `scripts\lab\lab_status.ps1`: every line must be **OK** | Claude |

Undo the laptop changes: `scripts\lab\setup_lab_network.ps1 -Undo`.

## Starting a new project from this template
1. Copy `config/lab.json`, `scripts/lab/`, `SETUP-LAB-NETWORK.cmd`, `SET-LAB-WIFI.cmd` into the new repo.
2. Keep the same lab router and addresses; only the firmware and server change.
3. Run `scripts\lab\lab_status.ps1` before every client demo.

## Health check (example of a fully green lab)
```
[OK] internet goes via household NIC   WiFi -> 192.168.0.1
[OK] laptop has pinned lab address     Ethernet = 192.168.124.4
[OK] lab network is Private            Private
[OK] mDNS advertises lab address       ionity-fleet.local -> 192.168.124.4
[OK] DNS logger on lab only            bind 192.168.124.4:53
[OK] esp32-...                         online, 192.168.124.x, fw 1.1.0
```
