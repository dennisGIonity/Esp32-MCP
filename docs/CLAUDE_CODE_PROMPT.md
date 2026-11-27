<!--
AEDI - IONITY GLOBAL | DOC-2026-11-ESP32MCP-PROMPT | Version 1.1 | 2026-11-11 SAST
Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
Policy 986 AED | License AED 900 | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - TM2
-->

# Claude Code prompt — finish ESP32-MCP NetObserve v3

Paste everything below the line into Claude Code. It can be started in `E:\.claude\Ionity\Esp32-MCP-v3` or in an empty folder; Step 0 rebuilds the repo either way.

---

You are finishing **ESP32-MCP NetObserve v3** for Ionity Global (Pty) Ltd. The latest code is **not on GitHub** (GitHub `main` stops at `e2f8287`, before v3). It exists only in a hand-over git bundle, so the first job is to rebuild the repo from it (Step 0). Then rebuild the work that was lost from the 23 Oct session and finish the rest (Steps 1-7).

## Step 0 — Rebuild the repo from the hand-over (do this first)

Hand-over zip: `E:\.PServer\Personal_Projects\MCP-Server\checkpoints\ESP32-MCP_Handover_2026-11-10.zip`
Inside it: `Esp32-MCP-netobserve-v3-2026-11-10.bundle` (full git history, branch `feat/netobserve-v3` at `34d37fe`), `source\` (plain copy of the same commit), `README_FIRST.md`, `TEST-RESULTS-2026-11-10.txt`.

1. Unzip it to `E:\.PServer\Personal_Projects\MCP-Server\checkpoints\ESP32-MCP_Handover_2026-11-10\` and run `git bundle verify` on the bundle. If the zip or bundle is missing or fails to verify, stop and tell Dennis.
2. Get the branch into a working repo. Pick the case that applies:
   - **Existing tree `E:\.claude\Ionity\Esp32-MCP-v3`** (branch `feat/netobserve-v3` at `2bfbc2f`). It shows ~143 modified files that are CRLF line-ending noise only. Prove that first: `git diff --ignore-cr-at-eol --stat` must print nothing and `git status --porcelain` must list no untracked (`??`) files. If either shows real changes, stop and show Dennis; do not discard anything. If clean:
     ```powershell
     git fetch "<path>\Esp32-MCP-netobserve-v3-2026-11-10.bundle" feat/netobserve-v3
     git reset --hard FETCH_HEAD        # safe only after the check above
     git add --renormalize . ; git status   # must be clean
     ```
   - **No usable tree**: `git clone -b feat/netobserve-v3 "<path>\Esp32-MCP-netobserve-v3-2026-11-10.bundle" E:\.claude\Ionity\Esp32-MCP-v3.1`, then `git remote set-url origin https://github.com/dennisGIonity/Esp32-MCP.git`.
   - Never work in `E:\.claude\Ionity\.ESP32-MCP\Esp32-MCP` (stale clone at `e2f8287`).
3. Confirm `git log --oneline -3` shows `34d37fe`, `d1a80dc`, `2bfbc2f` and that `origin` is `https://github.com/dennisGIonity/Esp32-MCP.git`. Do not push.
4. Rebuild the toolchain (nothing is installed in a fresh tree):
   ```powershell
   py -3.12 -m venv .venv ; .\.venv\Scripts\Activate.ps1      # 3.12-3.14 all work
   pip install -r services\fleet-host\server\requirements.txt -r services\network-sentinel\requirements.txt pytest-timeout pytest-asyncio ruff mcp==1.30.0
   npm ci                                                      # Node >= 22.18
   ```
   Firmware host tests need cmake, ninja, g++ and cJSON (`IDF_PATH` of an ESP-IDF 5.5 checkout, or `-DCJSON_DIR=<folder with cJSON.c>`); run them under WSL or Git Bash. If a tool is missing, say which one and carry on with the suites that can run.
5. Run the **Baseline** below and compare with `TEST-RESULTS-2026-11-10.txt`. Do not start Step 1 until the baseline matches; if it does not, report the difference first.
6. Read `docs/HANDOFF-2026-11-10.md`, `docs/PLAN-v3-ESP-CENTRIC.md`, `docs/architecture/PHASE-1-ARCHITECTURE.md` and `CHANGELOG.md`, then tell Dennis in one short paragraph what state the repo is in and what you will do next.

## Ground rules (non-negotiable)

- `dennisGIonity/Esp32-MCP` is never moved, renamed or transferred. A copy may go to the `Ionity-Global-Pty-Ltd` org later — only when Dennis asks.
- **Do not push, open PRs, force-push, rewrite history or delete branches without asking Dennis first.** Commit locally, small and often, one logical change per commit, imperative subject, body explaining why.
- Confirm every risky action first (flashing a board, binding port 53, changing router/DHCP settings, firewall rules, deleting files).
- Secrets live only in `E:\.claude\_vault`. Never print, log, commit or copy a token, key or Wi-Fi password. `.env` stays untracked.
- Keep the AEDI header (`Policy 986 AED | License AED 900 | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd`) on new source files, matching neighbouring files.
- The wire contract has one source: `packages/protocol/src` (TypeScript). After any contract change run `npm run gen -w @ionity/ewp` so `generated/ewp_protocol.h`, `services/fleet-host/server/app/ewp/ewp_protocol.py` and `devices/pi-agent/ewp_protocol.py` are regenerated — never hand-edit generated files. `npm run check:gen -w @ionity/ewp && npm run check:c -w @ionity/ewp` must pass.
- No fabricated data anywhere. If something cannot be measured, return an explicit "unavailable" state, never a plausible-looking number.
- Do not break the boardroom POC flow (`tools/packaging/poc`, `tools/packaging/testers`): laptop-as-DNS, YouTube flag, red LED.

## Baseline (must stay green; it was green on 2026-11-10)

```bash
# Python
cd services/fleet-host/server && IONITY_MQTT_ENABLED=false IONITY_MDNS_ENABLED=false python -m pytest -q --timeout 120   # 55
cd devices/pi-agent && python -m pytest -q tests                                                                      # 14
cd services/network-sentinel && python -m pytest -q                                                                  # 24
ruff check --select E9,F,B,ASYNC,S,RUF006 --ignore S101,S104,S105,S110,S311,S310,S603,S607,S608,B008 services/fleet-host/server/app services/fleet-host/server/mcp_stdio_proxy.py devices tools/scripts
# TypeScript
npm ci && npm run typecheck && npm test && npm run build -w @ionity/portal                                            # 51
# Firmware host (needs cmake, ninja, g++; cJSON via IDF_PATH or -DCJSON_DIR)
IDF_PATH=<esp-idf> bash firmware/esp32-node/test/run.sh                                                              # 94 + fuzz + interop 7/7
```

## Work, in this order

Steps 1-4 **rebuild work that was done on 23 Oct but lost** before it was saved: it is in no commit, bundle or folder, so do not search for it; write it again from the specs here.

### Step 1. `ping_host` — EWP/1 tool 0x47, end to end
- **Protocol** (`packages/protocol/src/ids.ts`, `netobserve.ts`, `tools.ts`): `PING_HOST: 0x47` in the NetObserve range. Args: `target` (hostname or IPv4, ≤ 253 chars), `count` (1-10, default 4), `timeout_ms` (100-5000, default 1000), `interval_ms` (≥ 200). Result: `resolved_ip`, `sent`, `received`, `rtt_min_ms`, `rtt_avg_ms`, `rtt_max_ms`, `error` (enum: none / resolve_failed / timeout / not_permitted / busy). Validate bounds in the encoder and decoder; add cases to `packages/protocol/test/netobserve.test.ts` (node --test) incl. out-of-range rejection. Regenerate C + Python.
- **Firmware** (`firmware/esp32-node/components/netobserve` + `mcp_server`): implement with ESP-IDF `ping_sock.h` (`esp_ping_new_session`) behind the existing `EspNetPlatform` interface so the host build uses a fake. One ping session at a time (`busy` otherwise), never block the MCP task — run async and reply on completion. Host unit tests with the fake platform (success, partial loss, resolve fail, busy, bounds). Add an interop case in `test/interop/interop.test.mjs`. Bump firmware to **3.2.0**.
- **Pi agent** (`devices/pi-agent`): unprivileged ICMP via `socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP)`; if `EPERM`/`EACCES` fall back to raw socket; if both fail return `not_permitted`. No `subprocess ping`. Tests mock the socket. Bump to **2.1.0**.
- **Fleet host**: node action `ping_target` (REST under the existing node-actions route, admin-token protected, rate-limited like other actions) and MCP tool `ping_from_node(device_id, target, count?)` with an accurate description and input schema. Tests: REST + MCP over Streamable HTTP and stdio (extend `test_e2e_mcp_dns.py` style).
- **Portal**: "Ping from this board" in `BoardCard` (target input, result table, error states). vitest for the API client.

### Step 2. Firmware MQTT reconnect back-off with jitter
`components/net/src/MqttLink.cpp` currently uses a fixed `reconnect_timeout_ms = 5000`. Set `mc.network.disable_auto_reconnect = true` and drive reconnects from an `esp_timer`: delay 1 s doubling to a 60 s cap, ±20 % jitter (`esp_random`), reset to 1 s on `MQTT_EVENT_CONNECTED`, and pause while Wi-Fi is down (the `WifiManager` already tracks up/down). Put the delay maths in a small pure `Backoff` class with host unit tests (sequence, cap, jitter bounds, reset). Keep the `reconnects()` counter in telemetry.

### Step 3. Fleet host MQTT bridge start-up
The 23 Oct note says a start-up crash was fixed but the fix is not in the saved code. Reproduce: start the host with `IONITY_MQTT_ENABLED=true` (a) broker down, (b) broker up, (c) broker going down and up again. The app must start, report MQTT as degraded in `/api/v1/health`, and reconnect with back-off — never crash or block start-up. Add a test with a local Mosquitto (or `amqtt`/`hbmqtt`-style in-process broker if Mosquitto is not available) marked so CI can run it.

### Step 4. Network sentinel — real data only
`services/network-sentinel/core`: `probe_engine.py`, `security_analyzer.py`, `mikrotik_collector.py`, `traffic_analyzer.py`, `loadshedding.py` still invent values with `random`.
- Probes: real ICMP (same unprivileged approach as the Pi agent) or TCP connect timing; jitter from real samples only.
- Remove `simulate_random_threat`. Threats come from RouterOS REST `/rest/log` (firewall topics); document that firewall rules need `log=yes`.
- MikroTik: parse RouterOS REST `/rest/interface` (rx/tx bytes, deltas over time). Make `floor_interfaces` an explicit config mapping (no guessing); unknown interfaces listed as unmapped.
- `simulate_if_unavailable` / `simulate_if_offline` default **false**; if a dev turns them on, every simulated value is labelled `"simulated": true` and the dashboard shows it.
- Load-shedding: no hard-coded stage. Either a configurable provider (e.g. EskomSePush API with a key from env) or `unavailable`.
- Tests with recorded RouterOS JSON fixtures; a grep test that fails if `import random` appears in `core/` outside clearly labelled simulation code.

### Step 5. CI (`.github/workflows/ci.yml`)
Add: an MQTT E2E job (Mosquitto service container, host + Pi agent node over a real broker); a firmware host-test job (pin cJSON to a tag, pass `-DCJSON_DIR`); an ESP-IDF build job for `esp32s3` and `esp32c6` (`espressif/idf` container pinned to the 5.5 release the README uses) that fails on warnings; the Playwright portal e2e. Keep jobs cached and parallel. Do not change `pages.yml`/`docker-image.yml` unless they break.

### Step 6. Docs and versions
Update `README.md`, `CHANGELOG.md` (3.0.0-rc → final entries for each item), `docs/DEVICE-MCP.md` (new tool), `docs/LAN-DNS-VISIBILITY.md`, and a new `docs/HANDOFF-<date>.md` that says exactly what is verified, what is not, and how.

### Step 7. Live board test — prepare, do not run unattended
Write `tools/scripts/live_check.py` + a short checklist in `docs/LAB.md` for Dennis to run with real boards: provision via portal → router DHCP DNS points at the ESP node → open YouTube on a phone → LED red + flag in portal + `dns_recent` via MCP → `ping_from_node` → pull the broker cable and watch back-off. Stop and ask Dennis before anything that touches the router, port 53 or a board.

## Definition of done
- All baseline suites green plus the new tests; ruff clean; `check:gen` and `check:c` clean; S3 and C6 builds with 0 warnings.
- No `random`-driven values in production paths.
- Every item in `HANDOFF-2026-11-10.md` §6 either done or written up with the reason it is not.
- A final summary to Dennis: commits made, test counts, what still needs a human (live boards, push/PR approval).
