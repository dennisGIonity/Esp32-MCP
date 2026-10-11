<!-- AEDI - IONITY GLOBAL | @ionity/prov | Policy 986 AED | License AED 900 | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd -->

# @ionity/prov — `ionity-prov/2` serial provisioning

The contract between the provisioning portal (and later the gateway's enrolment API) and the ESP32 firmware
for writing board settings into NVS over a serial line. Backward compatible with `ionity-prov/1`
(firmware 2.x `Provision.ino`). Spec and firmware obligations: `docs/architecture/PHASE-2-PORTAL.md` §4–5.

| Module | What it does |
|---|---|
| `framing.ts` | Request/reply lines, `IONITY-PROV ` prefix, CRC-32 suffix, line limits (767 fw 2.x, ≥ 4096 fw 3) |
| `crc32.ts` | CRC-32/ISO-HDLC, check value `0xCBF43926` |
| `fields.ts` | `set` wire fields → NVS keys (namespace `ionity`), limits, secret list, `redact()` |
| `host-url.ts` | MCP host URL parsing; canonical form `scheme://host:port[/path]`; fw 2.x `server`/port mapping |
| `uuid.ts` | RFC 9562 UUIDv7 for device identities |
| `schema.ts` | The provisioning form (zod/mini) and `validateForm()` with field-level messages |
| `compat.ts` | `planSet(form, board)`: only what this firmware accepts; errors instead of silent loss |
| `board.ts` | One normalised `BoardInfo` / `TestResult` for prov/1 and prov/2 replies |
| `client.ts` | `ProvClient` over any `LineTransport`: correlation, retries, CRC check, handshake |
| `testing/fake-board.ts` | Simulated fw 2.x / fw 3 board (`@ionity/prov/testing`) for tests and the portal simulator |

Plain TypeScript with no DOM or Node typings: runs in the browser (Vite) and in Node ≥ 22.18 (type stripping).

```bash
npm test -w @ionity/prov        # 15 tests: protocol + client against simulated fw 2.x and fw 3
npm run typecheck -w @ionity/prov
```

Secrets (`pass`, `mqtt_pass`, `fleet_token`, `mcp_token`, `ota_pass`) are never logged by the client
(`redact()`) and never echoed by a conforming board (it reports `has_*` flags).
