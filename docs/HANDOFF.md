# Ionity ESP32-MCP Lab — Handoff (2026-09-23)
Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | www.ionity.today

## Done
- fw 1.1.0 on both ESP32-S3s (COM8 esp32-98a316e5d18c, COM10 esp32-fc012cd8ea14), online via MQTT.
- OLED auto-detect: common pins + full safe-GPIO I2C scan, NVS cache, `set_display` command (server restarted, tests 5/5).
- Result: NO I2C display on either board -> OLED is on another board (one of the 2 not enumerating on USB) or is SPI/parallel.
- GateFlame disabled on the Pi 5 (user confirmed). Resume: `E:\.IONITY-LAB\RESUME-GATEFLAME.cmd`.
- Pi display script ready: `E:\.IONITY-LAB\SETUP-PI-LAB.cmd` (ASUS screen kiosk of the dashboard).
- **2026-09-23: the lab moved into its own project, Ionity-Lab (`E:\.IONITY-LAB`).**

## Network target (user decision)
- TP-Link / Afrihost main router: leave as is. Laptop **WiFi = Afrihost** (192.168.0.3, internet).
- **Lab = H3C Magic** (192.168.124.x). Laptop **Ethernet = H3C** (lab only).

## Laptop network state found (not yet changed — needs admin)
- WiFi: Afrihost, 192.168.0.3, metric 35, Private, internet OK.
- Onboard `Ethernet` (Realtek PCIe, 1 Gbps) is UP but has 169.254.x -> no DHCP from H3C
  (check cable is in a H3C LAN port, not WAN).
- `Ethernet 3` (Realtek USB NIC) disconnected but holds stale 192.168.124.2 + default route metric 0.
- Ethernet profile = Public "Unidentified network" -> firewall would block lab traffic (1883/8099/53/5353).
- No Ionity fleet firewall rules exist (only generic Python allows).

## Next steps (tomorrow)
1. Confirm cable -> H3C LAN port; renew DHCP on `Ethernet`.
2. Elevated script: WiFi metric 10 (internet), Ethernet metric 50 + no default gateway / DNS on H3C side
   (lab subnet route only), set Ethernet profile Private, add firewall rules 1883, 8099, 53/udp, 5353/udp.
3. Pin laptop IP on the H3C (DHCP reservation) -> set `IONITY_MDNS_ADVERTISE_IP` to it.
4. Get H3C WiFi SSID/password -> reflash ESPs (secrets.h) so the lab boards join the H3C.
5. Move Pi 5 to H3C; run `E:\.IONITY-LAB\SETUP-PI-LAB.cmd`; read `E:\.IONITY-LAB\data\pi-lab-setup.log`.
6. OLED: identify the OLED board (name/photo), get it on USB, flash with add_device.ps1.

## 2026-09-24: lab switched OFF (checkpoint)
- All work committed and pushed (ESP32-MCP `575b394`, lab repo `6530ac9`).
- Stopped: lab broker, fleet server + dashboard, serial bridge, MCP bridge. Nothing listens on 1883/8099/53.
- Restarts at logon (Startup shortcut) or when an ionity-esp32-fleet MCP tool is called. By hand: `E:\.IONITY-LAB\START-LAB.cmd`.
- Full status and open items: https://github.com/dennisGIonity/Ionity-2nd-Router-Test-Lab/blob/main/docs/STATUS.md

## 2026-09-28: top-to-bottom review (MCP server + deployables)
- **MCP server 1.3.0**: protocol negotiation 2025-06-18 / 2025-03-26 / 2024-11-05 (shared by server + stdio bridge),
  instructions, 2 prompts, schema-driven argument validation (limits clamped), tool titles + read-only /
  destructive annotations, structuredContent, notifications -> 202, JSON parse errors.
- **Security**: optional `IONITY_ADMIN_TOKEN` guards every command (REST /cmd, MCP send_command); the bridge
  reads it from .env, the dashboard asks once. Empty = open lab mode (unchanged behaviour).
- **Bridge bug fixed**: a backend *timeout* returned a protocol error (clients can drop the server); now a readable tool error.
- **New deployable**: Raspberry Pi Zero / Linux agent `devices/pi-agent` (same protocol + commands as the ESP32),
  systemd installer. Verified live end to end: registered over MQTT, ping -> pong, set_meta without reboot, Last Will -> offline.
- **Firmware fixes**: sketch.yaml no longer pins COM ports (compile failed with the board unplugged) and lists U8g2;
  PlatformIO now builds the one Arduino sketch (stale v1.0.0 source with hard-coded 192.168.2.11 retired).
- **Tests**: 5 -> 23 server tests (MCP contract, HTTP transport, bridge offline, Pi agent) + 13 sentinel tests, all passing.
- Not verifiable on this PC: PlatformIO pioarduino build (Smart App Control blocks its Python helpers). Arduino CLI builds verified.
- Heads-up: drive C: has 52 GB free of 953 GB (5.5%).

## 2026-09-29: revamp/v2 - flasher, on-device MCP, Datadog, MCP host fixes
- **Why boards were offline:** laptop on WiFi 192.168.0.2, H3C Ethernet without DHCP, no board on USB, and
  `.env` pinned `IONITY_MDNS_ADVERTISE_IP=192.168.124.4` (H3C NIC, not present) -> mDNS sent every board to
  an address nothing could reach. The host now falls back to the routed LAN IP and logs why.
- **WAL bug:** `data/fleet.db-wal` was 150 MB beside a 13 MB DB. `journal_size_limit` + `wal_checkpoint(TRUNCATE)`
  at start and after each prune; now 0 bytes after restart.
- **Hung shutdown/tests:** cancelling the writer mid-aiosqlite-query deadlocked close(); the writer now drains.
- **fw 2.0.0:** NVS + serial provisioning (ionity-prov/1), on-device MCP (HTTP :80/mcp + MQTT), edge inference,
  actuators, state modes. All 4 images compile (S3 uart/usb 62 %, classic 63 %, C3 68 %). Not yet flashed to
  hardware - no board was on USB.
- **Flasher** at `/flasher/` (React, esptool-js 0.7). **Datadog** forwarder off until `IONITY_DD_API_KEY` is set.
- **MCP host 2.0.0:** `device_list_tools`, `device_call_tool`, `integrations_status`, `set_state_mode`.
  Verified live: host -> broker -> `scripts/device_emulator.py` -> reply in 3-4 ms, FAILSAFE lock honoured.
- Independent code review (no hardware) fixed before commit: WiFi never started inside setup's 20 s
  wait (retry timer), serial RX buffer set after begin() (ignored in core 3.x), MQTT retries starving
  the loop on HTTP fallback, full I2C scan toggling actuator pins at boot, scan during connect; flasher:
  handshake spin, read loop dying on break/framing errors, native-USB re-enumeration needing a new click,
  close() racing the reader lock.
- Tests: 36 server (was 23) + 10 flasher. CI + Pages workflows added.
- Next: plug a board in, open http://localhost:8099/flasher/, Flash & provision; set `IONITY_DD_API_KEY`;
  fix `.env` (`IONITY_MDNS_ADVERTISE_IP`, `IONITY_DNS_BIND`) for whichever network the lab is on.

## 2026-09-29 (afternoon): fw 2.0 on the lab hardware
- Fleet launched with `scripts\start_lab.ps1`: broker :1883, fleet server :8099, serial bridge; mDNS
  `ionity-fleet.local -> 192.168.0.2` (laptop WiFi; the pinned 192.168.124.4 is now bypassed automatically).
- Flashed with esptool (merged images from `firmware/build.py`, hash verified):
  - COM3  (CH340)      esp32-98a316e5d18c  `esp32s3_uart.bin`  label "lab-node-01 (S3 16MB, CH340)"
  - COM10 (native USB) esp32-fc012cd8ea14  `esp32s3_usb.bin`   label "lab-node-02 (S3, native USB)"
- First real boot of fw 2.0 on both: banner, `IONITY-PROV hello`, `set` (server 192.168.0.2, site lab,
  group bench, label) and `reboot` all answered over serial via `scripts/provision.py`.
- Flashing rewrote NVS, so the old lab WiFi is gone: both boards wait for WiFi. Owner enters the
  household 2.4 GHz WiFi in the flasher (Provision only) - credentials are not stored in the repo.
- Next: after WiFi, run `fleet_summary`, `device_call_tool read_telemetry` / `run_inference` on both.

## 2026-09-29 (evening): full lab up - Pi-attached board deployed, lab WiFi missing
- Laptop: WiFi 192.168.0.2 (household) + Ethernet **192.168.124.2** on the H3C (lab.json expects .4 - no DHCP
  reservation yet). Host now picks the same-/24 address for mDNS + LAN DNS automatically: ionity-fleet.local ->
  192.168.124.2, LAN DNS bound on 192.168.124.2:53.
- Pi 5 (wabapi@192.168.124.3, eth0 only, **no internet, clock ~8 days behind** - no NTP) has lab-node-01
  (esp32-98a316e5d18c, CH340, /dev/ttyUSB0). Deployed with `scripts/deploy_pi_board.ps1` + offline esptool wheels
  (`~/ionity-flash/venv`), lab image from `firmware/build.py --lab` (secrets.h baked in, full erase -> NVS seeded).
- **Blocker:** the board reports WiFi reason **201 SSID not found** for IONITY-LAB-IOT, and the Pi's own scan
  (nmcli) doesn't see it either -> the H3C 2.4 GHz IoT network is off/hidden. The board retries every 12 s and will
  join by itself once it is broadcasting.
- Firmware fixes from the hardware run: WiFi retry no longer calls begin() while the driver is mid-connect
  ("sta is connecting, cannot set config"), 12 s retry, disconnect reason captured (own STA_LEAVING ignored) and
  reported in hello/get/test + serial log; hello MAC read from eFuse (was 00:00:.. before WiFi start); scan
  includes hidden SSIDs and reports failure. New `scripts/serial_log.py`.
- lab-node-02 (esp32-fc012cd8ea14, native USB) still on the laptop, COM10 held by another program.

## 2026-10-03: audit patches landed, lab WiFi still mismatched
- Committed the 2026-10-02 audit (docs/AUDIT-2026-10-02.md): pinned server requirements, offline-buffer timestamp
  fix (F-03), closure/task fixes, sentinel import fix (NameError: Optional), mosquitto ACL, firmware changes.
  Verified here: server 42 tests pass, sentinel 24 pass, fw 2.0.0 S3 uart/usb images compile (62 %).
- H3C was factory-reset 2026-09-29; wizard named the networks **Ionity-LAB_2.4G** / **Ionity-LAB_5G**. secrets.h and
  lab.json still say IONITY-LAB-IOT, so lab-node-01 (now on the laptop, COM3) reports reason 201 SSID not found.
  Fix: flasher "Provision only" with SSID Ionity-LAB_2.4G, or rename the 2.4 GHz SSID back on the router, or run
  SET-LAB-WIFI.cmd with the new name + password and reflash --lab.
- Pi 5: eth0 192.168.124.3 (lab, no default route; DHCP static entry added on the H3C), wlan0 Afrihost (household
  internet, default route). Internet + NTP OK. Laptop lab NIC = 192.168.124.2 (host adapts; pin .4 or update lab.json).
- lab-node-02 (native USB) was online on the household WiFi on 09-29; currently unplugged/offline.

## 2026-10-03 (night): both boards online, fw 2.1.0 WiFi rescue
- lab-node-01 esp32-98a316e5d18c: laptop COM3 (CH340), 192.168.124.4. lab-node-02 esp32-fc012cd8ea14: Pi /dev/ttyACM0
  (native USB), 192.168.124.5. Both on **Ionity-LAB_2.4G**, host 192.168.124.2, MQTT, MCP answering (read_telemetry
  85-114 ms, run_inference, identify). secrets.h + lab.json SSIDs updated to Ionity-LAB_2.4G / Ionity-LAB_5G.
- Why they were not picked up: the router reset renamed the SSID. Fix going forward = fw 2.1.0 WiFi rescue
  (known networks) + `set_wifi` broadcast before any router change. See CHANGELOG 2.1.0.
- H3C: DHCP static entry for the Pi's eth0 (192.168.124.3) was added on 09-29 (confirm under Interface → DHCP Static
  List); laptop is .2 (lab.json still says .4 - pin it or change lab.json).
- Pi: wlan0 = household internet (default route), eth0 = lab only. Pi flash toolchain in ~/ionity-flash (offline wheels).
- fw 2.1.1: USB-CDC TX timeout 5 ms (native-USB board stalled 8 s per MCP call while a closed serial session
  left the CDC "connected"). Both boards answer MCP in ~100-150 ms through the host.

## 2026-10-03 (early): final check, dashboard polish, testers package
- **Server**: health OK, MQTT connected, MCP 1.3.0 (17 tools). Live fleet: `esp32-98a316e5d18c` fw 2.1.0 @ .124.4 and
  `esp32-fc012cd8ea14` fw 2.1.1 @ .124.5 online, Pico offline. Fake devices (`esp32-emu%`, `pi-e2e%`) purged. Tests 43/43.
- **Dashboard audit**: every control exercised against the live fleet (search, health/site/group filters, card click and
  Enter, drawer ✕/Escape/backdrop, Ping/Identify, DNS search + window, MCP console, flasher link). No console errors,
  no horizontal scroll at 375 px, no fake data. DNS panel is empty because the router does not hand out the server as DNS.
- **Dashboard 2.1.2**: Ping/Identify/Reboot show the board's actual reply + round-trip (`pong · 398 ms`) and a toast;
  Reboot and Identify-all confirm first; focus rings, toasts, Ctrl+Enter in the MCP console.
- **Testers package**: `scripts\build_testers_package.ps1` → `dist\Ionity-ESP32-MCP-Testers-v<ver>.zip`. Verified on this
  PC in a copy on alt ports (8199/1993, mDNS off): SETUP → START → demo device + ping round trip → RUN-TESTS 43/43 → STOP
  (only its own processes; the lab on :8099 stayed up).
- **Note**: `.env` still advertises `IONITY_MDNS_ADVERTISE_IP=192.168.124.4` but the laptop's lab IP is now .124.2 (a board
  holds .4). The server logs/uses .124.2 and boards connect, but set the .env value to .124.2 (or blank) at the next restart.
