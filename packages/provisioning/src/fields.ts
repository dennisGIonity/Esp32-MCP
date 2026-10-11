/*
 * AEDI - IONITY GLOBAL | @ionity/prov - ionity-prov/2 | fields.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Wire field names of the `set` operation and the NVS keys they land in.
 * The NVS namespace is "ionity" (unchanged from fw 2.x, so a board updated to
 * fw 3 keeps its Wi-Fi). NVS keys are limited to 15 characters by ESP-IDF.
 */

/** Everything `set` can carry. Wire names are snake_case and never renamed. */
export interface SetFields {
  /** 1..32 bytes (802.11 SSID) */
  ssid?: string;
  /** "" = open network; 8..63 printable ASCII; 64 hex (raw PSK) on prov/2 only */
  pass?: string;
  /** prov/2: MCP host URL, e.g. mqtt://192.168.0.3:1883 · mqtts://fleet.ionity.lan · wss://gw/ewp */
  host_url?: string;
  /** prov/1 (fw 2.x): host, derived from host_url for older boards */
  server?: string;
  mqtt_port?: number;
  http_port?: number;
  /** prov/2: logical asset id, canonical lowercase RFC 9562 UUID */
  device_uuid?: string;
  /** prov/2: PEM CA certificate for mqtts:// / wss:// when not covered by the built-in bundle */
  ca_pem?: string;
  mqtt_user?: string;
  mqtt_pass?: string;
  fleet_token?: string;
  mcp_token?: string;
  ota_pass?: string;
  role?: 'node' | 'standalone';
  site?: string;
  group?: string;
  label?: string;
}

export type SetFieldName = keyof SetFields;

/** Where each field is stored. Keys <= 15 chars (NVS_KEY_NAME_MAX_SIZE - 1). */
export const NVS_KEY = {
  ssid: 'wifi_ssid',
  pass: 'wifi_pass',
  host_url: 'host_url',
  server: 'server',
  mqtt_port: 'mqtt_port',
  http_port: 'http_port',
  device_uuid: 'dev_uuid',
  ca_pem: 'ca_pem',
  mqtt_user: 'mqtt_user',
  mqtt_pass: 'mqtt_pass',
  fleet_token: 'fleet_token',
  mcp_token: 'mcp_token',
  ota_pass: 'ota_pass',
  role: 'role',
  site: 'site',
  group: 'group',
  label: 'label',
} as const satisfies Record<SetFieldName, string>;

/** Never echoed by the board, never shown in the terminal, never stored by the portal. */
export const SECRET_FIELDS = ['pass', 'mqtt_pass', 'fleet_token', 'mcp_token', 'ota_pass'] as const satisfies readonly SetFieldName[];
export type SecretField = (typeof SECRET_FIELDS)[number];

/** Fields fw 2.x (ionity-prov/1, Provision.ino) understands. Anything else it silently ignores. */
export const V1_SET_FIELDS: ReadonlySet<SetFieldName> = new Set<SetFieldName>([
  'ssid', 'pass', 'server', 'mqtt_port', 'http_port', 'mqtt_user', 'mqtt_pass',
  'fleet_token', 'mcp_token', 'ota_pass', 'role', 'site', 'group', 'label',
]);

/** Byte limits enforced here AND by the firmware (fw 2.x enforces ssid/pass/ports/role only). */
export const LIMITS = {
  ssidBytes: 32,
  passMin: 8,
  passMax: 63,
  hostUrlBytes: 128,
  labelBytes: 48,
  userBytes: 64,
  secretBytes: 128,
  caPemBytes: 3072,
} as const;

const SECRET_SET: ReadonlySet<string> = new Set(SECRET_FIELDS);

export function isSecretField(name: string): boolean {
  return SECRET_SET.has(name);
}

/** Copy of a request/fields object with every secret value replaced, for logs and terminals. */
export function redact<T extends Record<string, unknown>>(obj: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    out[k] = isSecretField(k) && typeof v === 'string' ? (v.length ? '•••' : '') : k === 'ca_pem' && typeof v === 'string' ? `<${v.length} chars>` : v;
  }
  return out as T;
}
