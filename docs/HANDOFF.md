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
