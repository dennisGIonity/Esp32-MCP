<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-009 | v1.0.0 | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# The test lab

A live bench of real boards reporting to the fleet server, isolated from
production by `site=lab`. GateFlame is deliberately **not** part of it and
stays untouched on the Pi 5.

## What is in it

| Label | Device ID | Hardware | Path to server |
|---|---|---|---|
| lab-node-01 | `esp32-98a316e5d18c` | ESP32-S3, 16 MB, CH340 USB-UART | WiFi → **MQTT** |
| lab-node-02 | `esp32-fc012cd8ea14` | ESP32-S3, native USB-Serial-JTAG | WiFi → **MQTT** |
| lab-node-03 | `pico-c18354253344139d` | Raspberry Pi Pico 2 (RP2350), no radio | USB → **serial bridge** → HTTP |

Every ID comes from the silicon (eFuse MAC / RP2350 chip ID), so nothing per
unit is ever compiled in.

## Starting it

It starts itself at logon (Startup shortcut **Ionity Lab**). By hand:

```powershell
E:\.ESP32-MCP\scripts\start_lab.ps1            # starts whatever isn't running
E:\.ESP32-MCP\scripts\start_lab.ps1 -Restart   # restart all three
```

That brings up, in order:

| # | Service | Port | Runs from |
|---|---|---|---|
| 1 | MQTT broker (amqtt) | 1883 | `infra\broker\run_broker.py` in `.venv-broker` |
| 2 | Fleet server + dashboard + MCP + LAN DNS | 8099, 53/udp | `server\run.py` in `.venv` |
| 3 | Serial bridge | — | `scripts\serial_bridge.py` in `.venv` |

The MCP bridge in Claude also runs `start_lab.ps1` if it finds the server down.

Dashboard: **http://192.168.0.3:8099/** — or whatever `ionity-fleet.local`
resolves to; the address is advertised over mDNS and boards follow it.

## Adding a board

**ESP32 (any: S3, C3, classic):**

```powershell
E:\.ESP32-MCP\scripts\add_device.ps1 -Port COM12 -Label "lab-node-04"
```

Detects the chip with esptool, picks the FQBN and flash size, compiles
(cached per chip type), flashes, waits for a *fresh* reading, then tags it.
If arduino-cli's upload fails on a native-USB board it retries with
`esptool --before usb-reset` automatically — see below.

**Raspberry Pi Pico / Pico 2 (no radio):** open
`firmware-arduino\Pico_MCP_Node` in the Arduino IDE, pick *Raspberry Pi Pico 2*
(or *Pico*), upload. The serial bridge picks up any RP2040/RP2350 port by
itself within 15 s. Give it a label in `config\device_labels.json` — edits apply
on the next reading, no restart.

**Pico W / Pico 2 W:** same sketch, pick the W board. It posts over WiFi *and*
emits serial, using `secrets.h` in the sketch folder.

## Commanding boards

Over MQTT, from the dashboard or from Claude via MCP:

| Action | Effect |
|---|---|
| `ping` | Round trip; replies `pong` on `cmd/result` (measured: 585 ms) |
| `identify` | Blinks the LED so you can find it on the bench |
| `set_meta` | Writes site/group/label to NVS and reboots to re-topic |
| `reboot` | Restart |

Serial-only boards (the Pico 2) cannot receive commands — there is no inbound
path to them. Label them in `config\device_labels.json` instead.

## Things this lab taught us

These are baked into the code now, but worth knowing:

1. **Never compile the server's IP into a board.** The router swap moved the LAN
   from `192.168.2.x` to `192.168.0.x` and the first board went silently dead.
   Boards now resolve NVS override → mDNS `ionity-fleet.local` → fallback, and
   re-resolve after 5 failed sends.
2. **ESP32-S3 native USB needs `usb-reset`, not DTR/RTS.** arduino-cli's upload
   recipe toggles DTR/RTS, which is right for CH340/CP210x bridges and wrong for
   the built-in USB-Serial-JTAG: the device drops off USB mid-connect and you get
   `PermissionError(13)` / Windows error 31. It looks exactly like a bad cable.
   It wasn't.
3. **aiomqtt does not work on Windows' default event loop.** It needs
   `add_reader()`, which the Proactor loop lacks. The server's MQTT bridge had
   never been able to connect on this host; with no broker running, it just
   looked like "connection refused". It now uses paho-mqtt on its own thread.
4. **A device row existing is not proof a device is alive.** `add_device.ps1`
   once reported success by matching a stale row. It now requires a reading
   that arrived after the reset.

## Still open

| Item | Needs |
|---|---|
| 2 more boards you mentioned | Plug them in; they did not enumerate on USB |
| Router DHCP → DNS `192.168.0.3` | Router admin login; the new router reset the old setting |
| Docker Desktop | First-run onboarding was never completed; the lab no longer depends on it |
| Pico 2 over Ethernet | Its previous firmware declared WIZnet W6x00 pins. If the board really has a W6100, it could report over wire instead of USB |

---

*Governance: Policy 986 AED · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd*
