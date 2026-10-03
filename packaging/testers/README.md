# Ionity ESP32-MCP — Testers Package

**AEDI · Ionity (Pty) Ltd** · Policy 986 AED · License AED 900 · Classification: TESTERS
© 2018-2026 Antwerp Designs | Ionity (Pty) Ltd — All rights reserved — TM2
Web: https://www.ionity.today · Ref: https://www.ionity.co.za · *Building Tomorrow, Today.*

A self-contained copy of the Ionity ESP32-MCP fleet stack for test benches:
MQTT broker, fleet server (REST + WebSocket + MCP), live dashboard, and the
browser flasher with ready-built ESP32 firmware. Open `MANUAL.html` for the
full illustrated manual.

## Requirements

- Windows 10/11 (no admin rights needed)
- Python 3.11 or newer — https://www.python.org/downloads/ (tick **Add to PATH**)
- Chrome or Edge for the flasher (Web Serial)
- Optional: an ESP32-S3 / ESP32-C3 / ESP32 board and a USB data cable

## Quick start

1. Unzip anywhere, e.g. `C:\Ionity\ESP32-MCP-Testers`.
2. Double-click **SETUP.cmd** (once; 1–3 minutes).
3. Double-click **START.cmd** — the dashboard opens at http://127.0.0.1:8099/.
4. Flash a board: **FLASH-BOARD.cmd** → *Select USB port* → *Identify chip* → WiFi name/password →
   **Flash & provision**.
   The board appears on the dashboard within ~30 s.
5. No hardware? **DEMO-DEVICE.cmd** adds one clearly labelled simulated device;
   **PURGE-DEMO.cmd** removes it again.

## The .cmd files

| File | What it does |
|---|---|
| `SETUP.cmd` | Finds Python, creates `.venv` + `.venv-broker`, installs packages, writes `.env` |
| `START.cmd` | Starts broker (:1883) + fleet server (:8099), prints status, opens the dashboard |
| `STATUS.cmd` | Health, MQTT link, MCP version/tool count, device counts, URLs |
| `STOP.cmd` | Stops only the processes this package started |
| `OPEN-DASHBOARD.cmd` | Opens the dashboard |
| `FLASH-BOARD.cmd` | Opens the web flasher (`/flasher/`) |
| `RUN-TESTS.cmd` | Runs the server test suite (pytest) |
| `DEMO-DEVICE.cmd` / `PURGE-DEMO.cmd` | Add / remove one simulated device |

## What to test (checklist)

- [ ] STATUS shows `server UP`, `mqtt=True`, `mcp=2.0.1 (17 tools)`
- [ ] Dashboard KPIs match the real number of boards; live clock and ingest chart move
- [ ] Search, Health / Site / Group filters narrow the device grid
- [ ] Click a device (or Tab + Enter): drawer opens; Escape, ✕ and backdrop close it
- [ ] **Ping** shows `pong · NN ms` and a toast (real round trip from the board)
- [ ] **Identify** blinks the board LED; **Reboot** asks for confirmation first
- [ ] **Identify all** asks for confirmation, then blinks every online board
- [ ] MCP console: `fleet_summary`, `list_devices`, `get_device` return live JSON (Ctrl+Enter runs)
- [ ] Unplug a board: it turns *stale* then *offline* (Last Will) and the KPIs update
- [ ] RUN-TESTS: all tests pass

## Use with Claude Desktop (MCP)

Add to `%APPDATA%\Claude\claude_desktop_config.json` (adjust the path):

```json
{ "mcpServers": { "ionity-esp32-fleet": {
    "command": "C:\\Ionity\\ESP32-MCP-Testers\\.venv\\Scripts\\python.exe",
    "args": ["C:\\Ionity\\ESP32-MCP-Testers\\server\\mcp_stdio_proxy.py"] } } }
```

## Ports & firewall

`8099/tcp` dashboard + API · `1883/tcp` MQTT · `5354/udp` DNS logger (set `IONITY_DNS_PORT=53`
in `.env` to log a real LAN). Boards on other machines need Windows Firewall to allow
8099 and 1883 on a **Private** network — Windows prompts on first START; choose *Private*.

## Troubleshooting

- **"Python was not found"** — install Python 3.11+ with *Add to PATH*, reopen SETUP.cmd.
- **Server down** — read `logs\server_err.txt`. Port busy? change `IONITY_PORT` in `.env`.
- **Board never appears** — the board and PC must be on the same 2.4 GHz network; check the
  flasher's serial log for the server IP it found; allow 1883 in the firewall.
- **Commands say 401** — `IONITY_ADMIN_TOKEN` is set; the dashboard asks for it once.
- **Smart App Control blocks a .exe** — setup already uses pure-Python packages; rerun SETUP.cmd.

Report findings to **info@ionity.today** with the output of STATUS.cmd and the `logs` folder.
