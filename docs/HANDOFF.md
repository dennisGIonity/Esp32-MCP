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
