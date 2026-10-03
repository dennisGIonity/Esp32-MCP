# Ionity ESP32-MCP - A-to-Z Test Report

| | |
|---|---|
| Document ID | DOC-2026-10-ESP32MCP-TEST |
| Version | host 2.1.3 / MCP server 2.0.2 / firmware 2.1.1 |
| Date | 2026-10-03 (SAST) |
| Author | Johan Wilhelm van Antwerp - Ionity (Pty) Ltd - AEDI |
| Governance | Policy 986 AED - License AED 900 - CC BY-NC-SA 4.0 where stated |
| Classification | INTERNAL |
| Machine | Windows 11 Home 10.0.26220, Python 3.14.4, Node 24.15 |

Scope: everything a tester can touch - static analysis, unit tests, the live
stack (REST, MCP JSON-RPC over HTTP and stdio, WebSocket, MQTT, LAN DNS),
the dashboard in a real browser, the web flasher, the firmware images, every
utility script, and the testers package run cold from the zip on isolated
ports. Every line below was executed today; evidence is quoted, not assumed.

## Verdict

**Fully functional.** 0 open defects. Every surface passes with evidence.
Eight defects were found during this pass and fixed in the same commit
(section 4). What I can and cannot guarantee is stated plainly in section 6.

## 1. Static and unit

| Check | Result | Evidence |
|---|---|---|
| `pytest server/tests` | **44 passed** (43 before, +1 new) | `44 passed in 43.61s` |
| pyflakes, 38 Python files (server, scripts, devices, flasher, packaging) | **0 findings** | exit 0 |
| `python -m compileall` server scripts devices flasher | clean | rc 0 |
| `node --check dashboard/app.js` | clean | rc 0 |
| PowerShell parser on all 6 `.ps1` + `lab.ps1` | **0 parse errors** | per-file |
| ruff | *not run* | its binary is blocked by Windows App Control on this PC; pyflakes covers unused/undefined names |

## 2. Live stack (`scripts/smoke_live.py` - new, kept in repo and shipped in the package)

Run against the lab server (:8099 / MQTT :1883) and again against the
fresh package (:8199 / :1983). Both: **70 passed, 0 failed** in under 4 s.

| Area | What was proven | Evidence |
|---|---|---|
| REST | all 23 OpenAPI paths answer; every parameter-free GET returns 200; required-param routes 422; unknown device 404; unknown route 404; malformed JSON 400 | sizes 14 B - 4.5 KB, 1-50 ms |
| REST firmware | manifest lists 4 images fw 2.1.1; **all 4 .bin download**; missing image 404; `..%2F` traversal refused (400) | `esp32s3_uart, esp32s3_usb, esp32_classic, esp32c3_usb` |
| Dashboard assets | `/` and `/static/app.js` served | 200 |
| MCP | initialize (proto 2025-06-18), tools/list = **18 tools**, every tool has description + object schema, resources/list + read x3, prompts/list = 2 | 3-28 ms |
| MCP errors | unknown method -32601; unknown tool isError; missing required arg isError; **unknown device isError** (fixed today); malformed JSON-RPC -> -32700 envelope; batch handled | |
| MCP read tools | all 13 read-only tools called successfully | each <30 ms |
| WebSocket | `/ws/fleet` delivers `fleet_tick` frame | 1.4-2.0 KB |
| MQTT ingest | emulated board online via retained status in 0.3 s; telemetry queryable within 1 s | |
| Command echo/ping | `ping -> pong` round-trip via REST **29 ms**; `identify` via MCP send_command **18 ms**; reply visible in `get_command_results` by cmd_id | dashboard drawer showed `pong - 366 ms` with toast |
| Host -> broker -> board MCP | `device_list_tools` 6 tools 18 ms; `device_call_tool read_telemetry` 4 ms; `run_inference analog_threshold` ok; bad channel -> isError | |
| Device lifecycle | **`DELETE /api/v1/devices/{id}`** (new) forgets emulator in 14 ms: 3,690 rows gone, then 404, no server restart, **no zombie after 15 s** | |
| stdio MCP proxy (`server/mcp_stdio_proxy.py`, what Claude Desktop uses) | initialize / tools/list / tools/call / ping all answer over stdin-stdout | 4 replies, ids 1-4 |
| LAN DNS resolver | on the package (bind 0.0.0.0:5354): real A query `www.ionity.today` -> rcode 0, 5 answers, 348 ms upstream; second query **0 ms from cache**; both logged with client IP, `cached` flag; summary = 2 queries / 1 domain / 1 device | |

Lab note: on the lab server the resolver is *correctly* reporting
`bind failed` because `.env` still pins `192.168.124.4` and the laptop is on
`192.168.0.3` today (H3C lab network not connected). The dashboard shows
this honestly ("resolver DOWN - WinError 10049"). Not a code defect; the
pending `.env` fix is in HANDOFF.md.

## 3. Dashboard (built-in browser, `http://127.0.0.1:8099/`)

| Control | Result |
|---|---|
| Load | DOMContentLoaded 130 ms, load 201 ms; app.js 26 KB, style.css 17 KB, html 7 KB (all cached after first visit) |
| Status pills | `live` (WS), `mqtt 127.0.0.1:1883 OK`, `ionity-fleet.local -> 192.168.0.3`, clock ticking |
| KPIs vs reality | 4 devices / 1 online / 3 offline matched `/api/v1/fleet/summary` exactly |
| Search `smoke` | 1 of 4 shown |
| Health filter online / offline | 1 / 3 of 4 shown; reset -> 4 of 4 |
| Group filter `smoke` | 1 of 4 |
| Card click -> drawer | title, 11 identity rows, 10 live metrics, 40-row history, Identify / Ping / Reboot buttons |
| Drawer **Ping** | `pong - 366 ms` inline + toast `ping -> pong (366 ms)` |
| Drawer Close button, **Escape key** | both close |
| Identify all | confirm text `Blink the identify LED on all 1 online device(s)?`; toast `Identify broadcast to 1 online device(s)` |
| MCP console | fleet_summary runs; bad JSON -> `Invalid JSON arguments: ...`; tool change pre-fills args; **Ctrl+Enter** runs get_device |
| DNS panel | window select, search debounce, state line, feed count all react |
| Console errors | **0** across the whole session |
| Accessibility | 0 images without alt, 0 unnamed buttons, **0 unlabelled inputs** (6 fixed today), one aria-live region for toasts, focus-visible rings |
| Network behaviour | DNS panel now polls adaptively: 3 requests in the first 14 s instead of ~9; all pollers pause when the tab is hidden |

## 4. Defects found today - all fixed and re-tested

| # | Severity | Where | Defect | Fix |
|---|---|---|---|---|
| 1 | medium | `mcp/server.py` | `get_device` (and any tool returning a bare `{"error":...}`) answered `isError:false` - an LLM client would treat "unknown device" as success | wrapper flags error-only results; regression test added |
| 2 | medium | `api/routes.py` | malformed JSON body on `POST /telemetry`, `/devices/register`, `/devices/{id}/mcp` -> **500** | `_json_body()` helper -> 400 with reason; `/mcp/rpc` keeps the -32700 envelope; test added |
| 3 | medium | `tester.ps1`, `start_lab.ps1`, `lab.ps1` | `Listening()` used `Get-NetTCPConnection -State Listen`, which misses some Python sockets on Windows -> RUN-TESTS said "stack not running" while serving, and **duplicate brokers/servers were started** (3 broker pairs were found running) | real TCP connect test in all three scripts; duplicates stopped |
| 4 | medium | same three scripts | `Start-Process -Redirect*` let the servers inherit the launcher's stdout pipe: any wrapper that captures START output (CI, parent script, `START.cmd \| tee`) **hung until the server exited** | `Start-Detached` via WMI `Win32_Process.Create` + cmd redirection, hidden window; `start_lab.ps1` now returns in 8 s under capture |
| 5 | low | `registry` / purge | purging a device required a **server restart** (in-memory registry); smoke emulator left a ghost "offline" device | new `forget_device` (MCP, admin, destructive-annotated) and `DELETE /api/v1/devices/{id}`; PURGE-DEMO and the smoke use it; `purge_devices.py` hint updated |
| 6 | low | `mqtt_bridge.py` | every command reply logged in full at INFO (multi-KB MCP payloads per line) | truncated to 160 chars; full payload stays in DB / API |
| 7 | low | `dashboard/app.js` | DNS panel polled 3 requests every 5 s regardless of resolver state or tab visibility | adaptive 5 / 15 / 30 s, paused while hidden; alerts/health pollers also pause |
| 8 | low | `dashboard/index.html` | 6 form controls without accessible names | `aria-label` on each; cache-bust `?v=4` |

## 5. Testers package (cold run from the zip)

`dist/Ionity-ESP32-MCP-Testers-v2.0.2-fw2.1.1.zip` - 6.8 MB, 88 files,
safety gate passed (no `.env`, `secrets.h`, `fleet.db`, `dist-lab`, or
password literals). Extracted to a scratch folder, ports changed to
8199/1983 so it ran **beside** the live lab, then driven unattended by a
harness that captured all output (the exact scenario that used to hang):

```
setup        Python 3.14 found, .venv + .venv-broker, 42 s           rc=0
start        broker :1983 UP, server :8199 UP mqtt=True 18 tools     rc=0
status       dashboard / flasher / MCP URLs printed                  rc=0
demo         esp32-emu000000001=online within 8 s                    rc=0
demo (again) idempotent, "already running"                          rc=0
tests        44 unit passed + live smoke 70 passed / 0 failed        rc=0
purge-demo   forgot esp32-emu000000001: telemetry=7 ... devices=1    rc=0
             devices after purge: 0 - 15 s later: 0 (no zombie)
stop         6 processes stopped                                     rc=0
status       broker down, server down - 0 leftover processes
```

Flasher (`/flasher/`): loads, host URL auto-filled, "Host online - fw
2.1.1", `health` / `manifest` / `defaults` all 200, Web Serial available,
12 action buttons present, 0 console errors. Actual flashing needs a board
on USB - not exercised today (no board on the bench).

## 6. What this does and does not guarantee

Guaranteed by evidence above: every public route, every MCP tool, the
WebSocket feed, MQTT ingest and command round-trips, DNS resolution and
logging, every dashboard control, the flasher UI, every `.cmd` in the
package, firmware images downloadable and hash-listed, and clean start /
stop with no leftovers - on this machine, today, with an emulated board.

Not exercised today (needs hardware or the lab network): a real ESP32 on
USB being flashed; real boards publishing over the H3C lab network (all
three lab boards show *offline* because the laptop is not on
192.168.124.x); the Pi Zero agent on a live Pi; Datadog forwarding
(disabled, unit-tested only). Those paths are covered by unit tests and
were proven on earlier dates (HANDOFF.md), but "100 %" would require
re-running `RUN-TESTS.cmd` with the bench powered.

## 7. Resource profile

Fleet server after 9 h of uptime: 24 MB working set, 14 CPU-seconds.
SQLite 18 MB for 109 k rows, WAL bounded at 0.7 MB, every hot query uses
an index (`EXPLAIN QUERY PLAN` checked). Dashboard: 50 KB total assets,
no framework, one WebSocket, pollers sleep when the tab is hidden.
MQTT broker: 21 MB. Nothing phones home; the only outbound traffic is the
DNS resolver's upstream queries (1.1.1.1 / 8.8.8.8) when enabled.

---
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved - TM2
Building Tomorrow, Today. | Anything is Possible with God.
