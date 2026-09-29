<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-FLASH | v2.0.0 | 2026-09-29 SAST | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# Ionity ESP32 Flasher

A React page that flashes the fw 2.0 node image over **Web Serial** (esptool-js), then writes the
board's WiFi, MCP host and tokens into NVS over the same cable, and checks the result from the host.

| Where | URL | Notes |
|---|---|---|
| Fleet host | `http://<host>:8099/flasher/` | Recommended. Host, ports and (for admins) the fleet token are pre-filled; images come from `firmware/dist`; verification runs against the host. |
| GitHub Pages | `https://<owner>.github.io/<repo>/` | Images bundled by CI. An https page cannot call an http LAN host (mixed content), so verification is skipped there. |
| Dev | `cd flasher && npm run dev` | Vite on :5173, proxies `/api` to `localhost:8099`. |

Browser: Chrome, Edge or Opera on desktop. Firefox and Safari have no Web Serial.

## What one click does

1. **Identify** — esptool-js resets the chip into the ROM bootloader, reads chip, MAC, flash size.
   The USB vendor id tells a CH340/CP210x bridge (image `*_uart`, console on UART0) from Espressif's
   own USB (`0x303A`, image `*_usb`, console on USB CDC). The wrong one would flash fine and then never
   answer on serial, which is why the choice is automatic.
2. **Download + check** — the image named in the manifest, sha256 compared before anything is written.
3. **Flash** — optional full erase, then the merged image at `0x0`, compressed, ROM-verified (MD5).
   *Update only* reads the partition table inside the image and skips the NVS partition, so WiFi,
   MCP host and tokens survive.
4. **Boot + handshake** — the new firmware prints `IONITY-PROV {"op":"hello",...}`; an unprovisioned
   board repeats it every 5 s and waits.
5. **Provision** — `set` with the form fields (secrets are never echoed back or shown in the log).
6. **Test** — the board joins WiFi, resolves the host, connects MQTT and reports IP, RSSI and its
   MCP URL, without rebooting. A wrong password is reported here, not discovered an hour later.
7. **Reboot + verify** — the host must see the board online, and `tools/list` through the host must
   return the board's MCP tools.

## ionity-prov/1 (serial, 115200 8N1)

Host → board, one JSON object per line: `{"ionity":"prov","op":"<op>", ...fields}`
Board → host: `IONITY-PROV {"ok":true,"op":"<op>", ...}` (other lines are ordinary log output).

| op | fields | reply |
|---|---|---|
| `hello` / `get` | – | identity, fw, chip, flash, MAC, `provisioned`, ssid, server, ports, role, site/group/label, `has_pass`, `has_mcp_token`, mode, wifi, ip, mcp_url, host_resolved/host_via, mqtt |
| `set` | `ssid pass server mqtt_port http_port mqtt_user mqtt_pass fleet_token mcp_token ota_pass role site group label pins{pwm0,pwm1,relay0}` (all optional) | `changed[]` + the `get` fields |
| `scan` | – | `networks[]` (ssid, rssi, ch, open) |
| `test` | `timeout_ms` | `ok` + ip/rssi/host/mqtt, or `error` (ssid not found / wrong password / timeout) |
| `reboot`, `factory_reset` | – | `ok`, then the board restarts (factory_reset clears NVS) |

`server` may be an IPv4, a DNS name, or `name.local`; empty means mDNS `ionity-fleet.local`.
`role=standalone` = no host at all, the board is only an MCP server on the LAN.

CLI twin for scripts and browsers without Web Serial: `scripts/provision.py` (pyserial).

## A board plugged into the lab Pi (no browser, Pi offline)

```powershell
python firmware\build.py --lab esp32s3_uart            # image with the git-ignored secrets.h baked in -> firmware/dist-lab/
scripts\deploy_pi_board.ps1 -Image firmware\dist-lab\esp32s3_uart.bin -Label "lab-node-01" `
    -Wheels <esptool-aarch64.tar>                        # first run only: the Pi has no internet
```

The script copies the image, `provision.py` and `serial_log.py` over SSH (key from `lab.json`), installs
esptool into `~/ionity-flash/venv` from the offline wheels, erases, flashes, sets host/site/group/label and
runs the WiFi test. With a full erase the lab image seeds NVS from `secrets.h`, so no password is typed.
Watch the board: `ssh wabapi@192.168.124.3 ~/ionity-flash/venv/bin/python ~/ionity-flash/serial_log.py /dev/ttyUSB0 30 --reset`.

Build the wheel set once on any machine with internet:
`pip download esptool --platform manylinux_2_28_aarch64 --platform manylinux2014_aarch64 --platform any --python-version 3.13 --only-binary=:all:`
(esptool itself ships as an sdist: `pip wheel esptool --no-deps` first and add `--find-links .`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Failed to connect" in step 1 | Hold **BOOT**, tap **RESET**, release BOOT, retry. Some CH340 boards need it. |
| Flash OK, no hello | Native-USB board flashed with a `_uart` image (or the reverse) - pick the other image. Or tap RESET: the USB port re-enumerates after reset and the page asks you to re-select it. |
| `ssid not found` / reason 201 | ESP32 WiFi is 2.4 GHz only, and the network must be on. Use **Scan** to see what the board sees (hidden networks show as "(hidden)"). |
| reason 15 / 2 / 204 | Wrong WiFi password. |
| `wifi_error` in hello | fw 2.0 reports the driver's last disconnect reason in `hello` / `get` / `test`, and logs it on serial. |
| Online on the board, not on the host | The MCP host address is wrong or the broker is down; check `integrations_status` / `/api/v1/health`. |
