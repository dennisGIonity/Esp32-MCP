/*
 * AEDI - IONITY GLOBAL | @ionity/ewp | Python module generator | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Emits ewp_protocol.py - the same numbers as ewp_protocol.h - for the two
 * Python speakers of EWP/1: the fleet host (services/fleet-host) and the
 * Raspberry Pi agent (devices/pi-agent). Written to every target given on the
 * command line, so all copies are byte-identical:
 *
 *   node --experimental-strip-types scripts/gen-py-module.ts out1.py [out2.py ...]
 *
 * CI regenerates and fails on `git diff --exit-code` if a committed copy is stale.
 */
import { writeFileSync } from 'node:fs';
import * as ewp from '../src/index.ts';

const snake = (s: string): string =>
  s
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/([A-Z])([A-Z][a-z])/g, '$1_$2')
    .toUpperCase();

const out: string[] = [];
const line = (s = ''): void => {
  out.push(s);
};
/** Python literal (JSON with True/False/None; keys stay strings). */
const py = (v: unknown): string => {
  if (v === null || v === undefined) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  if (typeof v === 'number' || typeof v === 'string') return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(py).join(', ')}]`;
  if (typeof v === 'object') return `{${Object.entries(v as Record<string, unknown>).map(([k, x]) => `${JSON.stringify(k)}: ${py(x)}`).join(', ')}}`;
  throw new TypeError(`cannot render ${typeof v}`);
};

line('"""');
line('AEDI - IONITY GLOBAL | ewp_protocol.py - Edge Wire Protocol v1 (EWP/1)');
line('Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900');
line('(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd');
line();
line('AUTO-GENERATED from packages/protocol (scripts/gen-py-module.ts). DO NOT EDIT.');
line('Change the TypeScript contract and regenerate; CI rejects a stale copy.');
line('"""');
line('# fmt: off');
line('# ruff: noqa');
line();
line(`MAGIC = ${ewp.EWP_MAGIC}`);
line(`VERSION = ${ewp.EWP_VERSION}`);
line(`HEADER_LEN = ${ewp.EWP_HEADER_LEN}`);
line(`FRAME_FLAGS_KNOWN = ${ewp.FRAME_FLAGS_KNOWN_MASK}`);
line(`RULE_FLAGS_KNOWN = ${ewp.RULE_FLAGS_KNOWN_MASK}`);
line(`NO_TOOL = ${ewp.NO_TOOL}`);
line();
line('# hard limits');
line(`LIMITS = ${py(ewp.EWP_LIMITS)}`);
line();
line('# topics: ionity/ewp1/<site>/<hwid>/<channel>');
line(`TOPIC_ROOT = ${py(ewp.TOPIC_ROOT)}`);
line(`BROADCAST_TOPIC = ${py(ewp.BROADCAST_TOPIC)}`);
line(`CHANNEL = ${py(ewp.Channel)}`);
line(`CHANNEL_POLICY = ${py(ewp.CHANNEL_POLICY)}`);
line();

const enums: Record<string, Readonly<Record<string, number>>> = {
  MSG: ewp.MsgType,
  FLAG: ewp.FrameFlag,
  TOOL: ewp.ToolId,
  TOOL_FLAG: ewp.ToolFlag,
  ST: ewp.Status,
  MODE: ewp.StateMode,
  ACT: ewp.ActuatorKind,
  INPUT: ewp.InputSource,
  DTYPE: ewp.TensorDType,
  MODEL: ewp.ModelKind,
  LOG: ewp.LogLevel,
  RESET: ewp.ResetReason,
  CHIP: ewp.ChipModel,
  STATE: ewp.DeviceState,
  EVENT: ewp.EventKind,
  TM: ewp.TelemetrySection,
  DNS_MODE: ewp.DnsMode,
  DNSQ: ewp.DnsQueryFlag,
  RULE: ewp.RuleFlag,
  WATCH_OP: ewp.WatchlistOp,
  DISC: ewp.DiscoverySource,
  NETCAP: ewp.NetCap,
};
for (const [prefix, values] of Object.entries(enums)) {
  line(`# ${prefix}`);
  for (const [k, v] of Object.entries(values)) line(`${prefix}_${snake(k)} = ${v}`);
  line(`${prefix}_NAMES = ${py(Object.fromEntries(Object.entries(values).map(([k, v]) => [String(v), k])))}`);
  line();
}
line('# LLM-facing tool names by id');
line(`TOOL_NAME = {${Object.entries(ewp.TOOL_NAME).map(([id, n]) => `${id}: ${py(n)}`).join(', ')}}`);
line('TOOL_ID = {v: k for k, v in TOOL_NAME.items()}');
line(`RETRYABLE_STATUS = frozenset({${[...ewp.RETRYABLE_STATUS].join(', ')}})`);
line();
line('# CBOR map keys per body, field name -> integer key (all 0..23)');
line('KEYS = {');
const tables = Object.entries(ewp)
  .filter(([name, v]) => name.endsWith('Key') && typeof v === 'object' && v !== null)
  .sort(([a], [b]) => a.localeCompare(b));
for (const [name, table] of tables) line(`    ${py(name.slice(0, -3))}: ${py(table)},`);
line('}');
line('NAMES = {body: {v: k for k, v in t.items()} for body, t in KEYS.items()}');
line();
line('# built-in watchlist (src/watchlist.ts)');
line(`DEFAULT_WATCHLIST_VERSION = ${ewp.DEFAULT_WATCHLIST_VERSION}`);
line(`DEFAULT_CATEGORIES = ${py(ewp.DEFAULT_CATEGORIES)}`);
line(`DEFAULT_RULES = ${py(ewp.DEFAULT_RULES)}`);
line();
line('# header layout: (field, offset, size)');
line(`HEADER_LAYOUT = (${ewp.HEADER_LAYOUT.map((f) => `(${py(f.field)}, ${f.offset}, ${f.size})`).join(', ')},)`);
line();

const text = out.join('\n');
const targets = process.argv.slice(2);
if (targets.length === 0) process.stdout.write(text);
for (const t of targets) {
  writeFileSync(t, text);
  console.log(`wrote ${t} (${text.length} bytes)`);
}
