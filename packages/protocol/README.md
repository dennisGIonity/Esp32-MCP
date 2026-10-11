<!--
AEDI - IONITY GLOBAL | DOC-2026-10-ESP32MCP-EWP1 | v0.1.0 | 2026-10-11 SAST | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd | Classification: PUBLIC
-->

# @ionity/ewp — Edge Wire Protocol v1

The single wire contract between the ESP32-MCP gateway and every ESP32 node:
a 16-byte little-endian header followed by one integer-keyed CBOR body, over
MQTT 5 (primary), WebSocket or a bench serial link.

| File | Contents |
|---|---|
| `src/scalars.ts` | Branded wire scalars (`U8`…`U32`, `F32`, `Unit`, bounded `Utf8<N>`/`Bytes<N>`) with range-checked constructors |
| `src/ids.ts` | Message types, flags, tool ids, status codes, state modes, enums |
| `src/constants.ts` | Magic, version, hard limits, topic map, per-channel QoS/retain policy |
| `src/keys.ts` | `defineKeys<T>()` — CBOR key tables checked for completeness, uniqueness and 1-byte keys at compile time |
| `src/frame.ts` | Header layout + typed frame unions (downlink vs uplink) |
| `src/tools.ts` | Per-tool args/results + the `ToolContract` registry |
| `src/telemetry.ts`, `src/hello.ts`, `src/events.ts` | TELEMETRY, HELLO/STATUS, EVENT/LOG/CRASH bodies |
| `scripts/gen-c-header.ts` | Generates `generated/ewp_protocol.h` for the firmware |

```bash
npm run typecheck   # strict src + compile-time contract tests (TS 5.9 and 7.0 verified)
npm test            # runtime guards (Node 22+ type stripping)
npm run gen:c       # regenerate generated/ewp_protocol.h
npm run check:c     # header compiles as C11 and C++17 with -Wall -Wextra -Werror -pedantic
```

Never hand-edit a number on either side: change the TypeScript, regenerate the header.
Design rationale: [`docs/architecture/PHASE-1-ARCHITECTURE.md`](../../docs/architecture/PHASE-1-ARCHITECTURE.md).

---

*Governance: Policy 986 AED · Licence AED 900 · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd*
