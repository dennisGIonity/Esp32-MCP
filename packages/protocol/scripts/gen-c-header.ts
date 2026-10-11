/*
 * AEDI - IONITY GLOBAL | @ionity/ewp | C/C++ header generator | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Emits ewp_protocol.h for the ESP-IDF firmware from the TypeScript contract,
 * so numeric ids, CBOR keys, limits and the header layout exist in ONE place.
 *
 *   node --experimental-strip-types scripts/gen-c-header.ts [out.h]
 *
 * CI runs this and fails on `git diff --exit-code` if the committed header is stale.
 */
import { writeFileSync } from 'node:fs';
import * as ewp from '../src/index.ts';

const snake = (s: string): string =>
  s
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/([A-Z])([A-Z][a-z])/g, '$1_$2')
    .toUpperCase();

const hex = (n: number, w = 2): string => `0x${n.toString(16).toUpperCase().padStart(w, '0')}`;

const out: string[] = [];
const line = (s = ''): void => {
  out.push(s);
};

function enumBlock(cName: string, prefix: string, values: Readonly<Record<string, number>>, asHex = true): void {
  line(`typedef enum {`);
  for (const [k, v] of Object.entries(values)) line(`    ${prefix}_${snake(k)} = ${asHex ? hex(v) : String(v)},`);
  line(`} ${cName};`);
  line();
}

line('/*');
line(' * AEDI - IONITY GLOBAL | ewp_protocol.h - Edge Wire Protocol v1 (EWP/1)');
line(' * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900');
line(' * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd');
line(' *');
line(' * AUTO-GENERATED from packages/protocol (scripts/gen-c-header.ts). DO NOT EDIT.');
line(' * Change the TypeScript contract and regenerate; CI rejects a stale copy.');
line(' */');
line('#pragma once');
line();
line('#include <stddef.h>');
line('#include <stdint.h>');
line();
line('#ifdef __cplusplus');
line('#define EWP_STATIC_ASSERT(c, m) static_assert((c), m)');
line('extern "C" {');
line('#else');
line('#define EWP_STATIC_ASSERT(c, m) _Static_assert((c), m)');
line('#endif');
line();
line('/* Xtensa (ESP32/S2/S3) and RISC-V (C3/C6/H2/P4) are little-endian, like the wire. */');
line('EWP_STATIC_ASSERT(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "EWP wire format is little-endian");');
line();
line('/* ---- framing ----------------------------------------------------------- */');
line(`#define EWP_MAGIC                 ${hex(ewp.EWP_MAGIC)}u`);
line(`#define EWP_VERSION               ${ewp.EWP_VERSION}u`);
line(`#define EWP_HEADER_LEN            ${ewp.EWP_HEADER_LEN}u`);
line(`#define EWP_FRAME_FLAGS_KNOWN     ${hex(ewp.FRAME_FLAGS_KNOWN_MASK)}u`);
line(`#define EWP_NO_TOOL               ${hex(ewp.NO_TOOL)}u`);
line();
line('/* ---- hard limits (buffers are sized from these - never exceed) --------- */');
for (const [k, v] of Object.entries(ewp.EWP_LIMITS)) line(`#define EWP_LIMIT_${snake(k).padEnd(20)} ${String(v)}u`);
line();
line('/* ---- topics: ionity/ewp1/<site>/<hwid>/<channel> ----------------------- */');
line(`#define EWP_TOPIC_ROOT            "${ewp.TOPIC_ROOT}"`);
line(`#define EWP_TOPIC_BROADCAST       "${ewp.BROADCAST_TOPIC}"`);
for (const [k, v] of Object.entries(ewp.Channel)) {
  const pol = ewp.CHANNEL_POLICY[v];
  line(`#define EWP_CH_${snake(k).padEnd(19)} "${v}"   /* qos ${pol.qos}${pol.retain ? ', retained' : ''} */`);
  line(`#define EWP_CH_${(snake(k) + '_QOS').padEnd(19)} ${pol.qos}`);
  line(`#define EWP_CH_${(snake(k) + '_RETAIN').padEnd(19)} ${pol.retain ? 1 : 0}`);
}
line();
line('/* ---- identifiers ------------------------------------------------------- */');
enumBlock('ewp_msg_type_t', 'EWP_MSG', ewp.MsgType);
enumBlock('ewp_frame_flag_t', 'EWP_FLAG', ewp.FrameFlag);
enumBlock('ewp_tool_id_t', 'EWP_TOOL', ewp.ToolId);
enumBlock('ewp_tool_flag_t', 'EWP_TOOL_FLAG', ewp.ToolFlag);
enumBlock('ewp_status_t', 'EWP_ST', ewp.Status);
enumBlock('ewp_state_mode_t', 'EWP_MODE', ewp.StateMode, false);
enumBlock('ewp_actuator_kind_t', 'EWP_ACT', ewp.ActuatorKind, false);
enumBlock('ewp_input_source_t', 'EWP_INPUT', ewp.InputSource, false);
enumBlock('ewp_tensor_dtype_t', 'EWP_DTYPE', ewp.TensorDType, false);
enumBlock('ewp_model_kind_t', 'EWP_MODEL', ewp.ModelKind, false);
enumBlock('ewp_log_level_t', 'EWP_LOG', ewp.LogLevel, false);
enumBlock('ewp_reset_reason_t', 'EWP_RESET', ewp.ResetReason, false);
enumBlock('ewp_chip_model_t', 'EWP_CHIP', ewp.ChipModel);
enumBlock('ewp_device_state_t', 'EWP_STATE', ewp.DeviceState, false);
enumBlock('ewp_event_kind_t', 'EWP_EVENT', ewp.EventKind, false);
enumBlock('ewp_telemetry_section_t', 'EWP_TM', ewp.TelemetrySection);

line('/* LLM-facing names, for the optional on-device HTTP MCP front-end. */');
for (const [k, id] of Object.entries(ewp.ToolId)) {
  line(`#define EWP_TOOL_NAME_${snake(k).padEnd(20)} "${ewp.TOOL_NAME[id]}"`);
}
line();
line('/* ---- CBOR map keys (all 0..23 => one byte on the wire) ----------------- */');
const tables = Object.entries(ewp)
  .filter(([name, v]) => name.endsWith('Key') && typeof v === 'object' && v !== null)
  .sort(([a], [b]) => a.localeCompare(b));
for (const [name, table] of tables) {
  const base = snake(name.slice(0, -'Key'.length));
  line(`/* ${name.slice(0, -3)} */`);
  for (const [field, key] of Object.entries(table as Record<string, number>)) {
    line(`#define EWP_K_${`${base}_${snake(field)}`.padEnd(40)} ${String(key)}u`);
  }
}
line();
line('/* ---- fixed header --------------------------------------------------------');
line(' * memcpy() the first EWP_HEADER_LEN bytes of a payload into this struct;');
line(' * never cast the transport buffer pointer (its alignment is not guaranteed). */');
line('typedef struct __attribute__((packed)) {');
const cType = { 1: 'uint8_t ', 2: 'uint16_t', 4: 'uint32_t' } as const;
for (const f of ewp.HEADER_LAYOUT) {
  line(`    ${cType[f.size]} ${snake(f.field).toLowerCase().padEnd(12)}; /* @${String(f.offset).padStart(2)} */`);
}
line('} ewp_header_wire_t;');
line();
line(`EWP_STATIC_ASSERT(sizeof(ewp_header_wire_t) == EWP_HEADER_LEN, "EWP header must be ${ewp.EWP_HEADER_LEN} bytes");`);
for (const f of ewp.HEADER_LAYOUT) {
  line(
    `EWP_STATIC_ASSERT(offsetof(ewp_header_wire_t, ${snake(f.field).toLowerCase()}) == ${f.offset}, "layout: ${f.field}");`,
  );
}
line();
line('#ifdef __cplusplus');
line('} /* extern "C" */');
line('#endif');
line();

const text = out.join('\n');
const target = process.argv[2];
if (target) {
  writeFileSync(target, text);
  console.log(`wrote ${target} (${text.length} bytes)`);
} else {
  process.stdout.write(text);
}
