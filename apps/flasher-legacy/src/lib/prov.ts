// ===========================================================================
// AEDI - IONITY GLOBAL | ionity-prov/1 - the flasher side of Provision.ino
// Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
// ---------------------------------------------------------------------------
// One JSON object per line in each direction. Board replies are prefixed
// "IONITY-PROV " so they can be told apart from ordinary log output.
// Pure functions here; the port handling is in serial.ts.
// ===========================================================================

export const PROV_PREFIX = "IONITY-PROV ";

export type ProvOp = "hello" | "get" | "set" | "scan" | "test" | "reboot" | "factory_reset";

export interface BoardInfo {
  ok: boolean;
  op: string;
  error?: string;
  device_id?: string;
  fw?: string;
  product?: string;
  chip?: string;
  chip_rev?: number;
  flash_mb?: number;
  mac?: string;
  provisioned?: boolean;
  ssid?: string;
  has_pass?: boolean;
  server?: string;
  mqtt_port?: number;
  http_port?: number;
  role?: "node" | "standalone";
  site?: string;
  group?: string;
  label?: string;
  has_mcp_token?: boolean;
  has_ota_pass?: boolean;
  mode?: string;
  wifi?: "connected" | "down";
  ip?: string;
  rssi?: number;
  mcp_url?: string;
  host_resolved?: string;
  host_via?: string;
  mqtt?: boolean;
  changed?: string[];
  reboot_required?: boolean;
  networks?: { ssid: string; rssi: number; ch: number; open: boolean }[];
}

/** Everything the flasher can write. Undefined fields are left alone on the board. */
export interface ProvisionFields {
  ssid?: string;
  pass?: string;
  server?: string;
  mqtt_port?: number;
  http_port?: number;
  mqtt_user?: string;
  mqtt_pass?: string;
  fleet_token?: string;
  mcp_token?: string;
  ota_pass?: string;
  role?: "node" | "standalone";
  site?: string;
  group?: string;
  label?: string;
  pins?: { pwm0?: number; pwm1?: number; relay0?: number };
}

export function buildRequest(op: ProvOp, fields: Record<string, unknown> = {}): string {
  return JSON.stringify({ ionity: "prov", op, ...fields }) + "\n";
}

/** Returns the parsed reply if `line` is a provisioning line, else null. */
export function parseProvLine(line: string): BoardInfo | null {
  const i = line.indexOf(PROV_PREFIX);
  if (i < 0) return null;
  try {
    const obj = JSON.parse(line.slice(i + PROV_PREFIX.length).trim());
    return obj && typeof obj === "object" && typeof obj.op === "string" ? (obj as BoardInfo) : null;
  } catch {
    return null;
  }
}

/** Validation mirrors the firmware so the user sees the error before flashing. */
export function validateFields(f: ProvisionFields): string[] {
  const errs: string[] = [];
  if (f.ssid !== undefined && (f.ssid.length < 1 || f.ssid.length > 32)) errs.push("WiFi name must be 1-32 characters.");
  if (f.pass !== undefined && f.pass.length > 0 && (f.pass.length < 8 || f.pass.length > 63))
    errs.push("WiFi password must be 8-63 characters (leave empty for an open network).");
  for (const [k, v] of [["mqtt_port", f.mqtt_port], ["http_port", f.http_port]] as const)
    if (v !== undefined && (!Number.isInteger(v) || v < 1 || v > 65535)) errs.push(`${k} must be 1-65535.`);
  if (f.role && f.role !== "node" && f.role !== "standalone") errs.push("role must be node or standalone.");
  return errs;
}

export function provisionWarnings(f: ProvisionFields): string[] {
  const w: string[] = [];
  if (f.role !== "standalone" && (f.server ?? "").trim() === "")
    w.push("No MCP host set: the board will look for ionity-fleet.local over mDNS.");
  if (!f.mcp_token) w.push("No MCP token entered: a new board's own MCP endpoint stays read-only over HTTP (an existing token is kept).");
  if (f.pass === "") w.push("Empty WiFi password: only works on an open network.");
  return w;
}

/** Strip empty optional strings so "leave unchanged" really leaves them unchanged.
 *  Kept even when empty: pass (open network) and server (empty = mDNS). */
export function compactFields(f: ProvisionFields): ProvisionFields {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === null) continue;
    if (typeof v === "string" && v === "" && !["pass", "server"].includes(k)) continue;
    out[k] = v;
  }
  return out as ProvisionFields;
}

export function randomToken(bytes = 18): string {
  const a = new Uint8Array(bytes);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Hide secrets before a request is written to the on-screen log. */
export function redact(req: string): string {
  return req.replace(/"(pass|mqtt_pass|fleet_token|mcp_token|ota_pass)":"[^"]*"/g, '"$1":"•••"');
}
