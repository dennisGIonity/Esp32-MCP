/*
 * AEDI - IONITY GLOBAL | @ionity/prov - ionity-prov/2 | schema.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The provisioning form: what a person types, validated before a single
 * byte goes to the board. Client-side rules here mirror the firmware's
 * (fw 2.x Provision.ino, fw 3 contract) so errors appear on the field, not as
 * a timeout or a cryptic board reply.
 *
 * Empty optional fields mean "leave unchanged on the board".
 */
// zod/mini: same validation, a fraction of the browser bundle (the portal ships this file).
import * as z from 'zod/mini';
import { LIMITS } from './fields.ts';
import { parseHostUrl, type HostUrl } from './host-url.ts';
import { hasControlChars, isPrintableAscii, utf8ByteLength } from './text.ts';
import { normalizeUuid } from './uuid.ts';

/** Must stay identical to @ionity/ewp siteId(): it becomes an MQTT topic level. */
export const TOPIC_LEVEL_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;
const TOKEN_RE = /^[A-Za-z0-9._~-]{16,128}$/;
const HEX64_RE = /^[0-9a-fA-F]{64}$/;
const PEM_RE = /^-----BEGIN CERTIFICATE-----\r?\n[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\s*$/;

export const ProvisionFormSchema = z.object({
  ssid: z.string(),
  pass: z.string(),
  /** the network has no password - must be ticked explicitly */
  openNetwork: z.boolean(),
  /** keep the password already stored on the board (same SSID, field left empty) */
  keepPass: z.boolean(),
  /** "" = mDNS discovery of ionity-fleet.local */
  hostUrl: z.string(),
  deviceUuid: z.string(),
  caPem: z.string(),
  role: z.enum(['node', 'standalone']),
  site: z.string(),
  group: z.string(),
  label: z.string(),
  mqttUser: z.string(),
  mqttPass: z.string(),
  mcpToken: z.string(),
  fleetToken: z.string(),
});

export type ProvisionForm = z.infer<typeof ProvisionFormSchema>;
export type FormField = keyof ProvisionForm;
export type FieldErrors = Partial<Record<FormField, string>>;

export const EMPTY_FORM: ProvisionForm = {
  ssid: '',
  pass: '',
  openNetwork: false,
  keepPass: false,
  hostUrl: '',
  deviceUuid: '',
  caPem: '',
  role: 'node',
  site: '',
  group: '',
  label: '',
  mqttUser: '',
  mqttPass: '',
  mcpToken: '',
  fleetToken: '',
};

export interface ValidForm extends ProvisionForm {
  /** parsed host, or null for mDNS discovery */
  readonly host: HostUrl | null;
  /** canonical lowercase UUID, or "" */
  readonly uuid: string;
  /** pass is a 64-hex raw PSK rather than a passphrase */
  readonly rawPsk: boolean;
}

export type ValidationResult =
  | { readonly ok: true; readonly value: ValidForm; readonly warnings: readonly string[] }
  | { readonly ok: false; readonly errors: FieldErrors; readonly warnings: readonly string[] };

/** One message per field, in the order a person reads the form. */
export function validateForm(input: unknown): ValidationResult {
  const parsed = ProvisionFormSchema.safeParse(input);
  if (!parsed.success) {
    const errors: FieldErrors = {};
    for (const issue of parsed.error.issues) {
      const k = issue.path[0];
      if (typeof k === 'string' && k in EMPTY_FORM && !errors[k as FormField]) errors[k as FormField] = issue.message;
    }
    return { ok: false, errors, warnings: [] };
  }
  const f = parsed.data;
  const errors: FieldErrors = {};
  const warnings: string[] = [];

  // Wi-Fi --------------------------------------------------------------------
  const ssidBytes = utf8ByteLength(f.ssid);
  if (ssidBytes < 1 || ssidBytes > LIMITS.ssidBytes) errors.ssid = `Wi-Fi name must be 1-${LIMITS.ssidBytes} bytes (it is ${ssidBytes}).`;
  else if (hasControlChars(f.ssid)) errors.ssid = 'Wi-Fi name contains control characters.';

  let rawPsk = false;
  if (f.openNetwork) {
    if (f.pass !== '') errors.pass = 'Untick "Open network" or clear the password.';
  } else if (f.keepPass && f.pass === '') {
    // unchanged on the board
  } else if (HEX64_RE.test(f.pass)) {
    rawPsk = true;
  } else {
    const n = utf8ByteLength(f.pass);
    if (n < LIMITS.passMin || n > LIMITS.passMax) {
      errors.pass = f.pass === '' ? 'Enter the Wi-Fi password, or tick "Open network".' : `WPA2/WPA3 passwords are ${LIMITS.passMin}-${LIMITS.passMax} characters (this one is ${n} bytes).`;
    } else if (!isPrintableAscii(f.pass)) {
      warnings.push('The Wi-Fi password has non-keyboard (non-ASCII) characters. WPA2 expects plain ASCII; if the board cannot join, change the password on the router.');
    }
  }

  // Host -----------------------------------------------------------------------
  let host: HostUrl | null = null;
  if (f.hostUrl.trim() !== '') {
    const r = parseHostUrl(f.hostUrl);
    if (r.ok) host = r.value;
    else errors.hostUrl = `MCP host URL: ${r.error}.`;
  } else if (f.role === 'node') {
    warnings.push('No MCP host URL: the board will look for ionity-fleet.local over mDNS.');
  }

  // Identity -------------------------------------------------------------------
  let uuid = '';
  if (f.deviceUuid.trim() !== '') {
    const u = normalizeUuid(f.deviceUuid);
    if (u) uuid = u;
    else errors.deviceUuid = 'Device UUID must look like 0192f1c4-7d2a-7b3e-9a10-4c5d6e7f8a9b.';
  }

  // Fleet metadata ---------------------------------------------------------------
  for (const k of ['site', 'group'] as const) {
    if (f[k] !== '' && !TOPIC_LEVEL_RE.test(f[k])) errors[k] = `${k === 'site' ? 'Site' : 'Group'}: lowercase letters, digits and "-", 1-32 characters, starting with a letter or digit.`;
  }
  if (utf8ByteLength(f.label) > LIMITS.labelBytes) errors.label = `Label is limited to ${LIMITS.labelBytes} bytes.`;
  else if (hasControlChars(f.label)) errors.label = 'Label contains control characters.';

  // Security ---------------------------------------------------------------------
  if (utf8ByteLength(f.mqttUser) > LIMITS.userBytes || hasControlChars(f.mqttUser)) errors.mqttUser = `MQTT user: up to ${LIMITS.userBytes} bytes, no control characters.`;
  if (utf8ByteLength(f.mqttPass) > LIMITS.secretBytes || hasControlChars(f.mqttPass)) errors.mqttPass = `MQTT password: up to ${LIMITS.secretBytes} bytes, no control characters.`;
  for (const k of ['mcpToken', 'fleetToken'] as const) {
    if (f[k] !== '' && !TOKEN_RE.test(f[k])) errors[k] = 'Tokens are 16-128 characters: letters, digits, ".", "_", "~", "-".';
  }
  if (f.caPem.trim() !== '') {
    if (utf8ByteLength(f.caPem) > LIMITS.caPemBytes) errors.caPem = `CA certificate is limited to ${LIMITS.caPemBytes} bytes.`;
    else if (!PEM_RE.test(f.caPem.trim() + '\n')) errors.caPem = 'Paste one PEM certificate (-----BEGIN CERTIFICATE----- … -----END CERTIFICATE-----).';
  }
  if (host?.tls === false && (f.mqttPass !== '' || f.mcpToken !== '')) {
    warnings.push(`${host.scheme}:// is not encrypted: the MQTT password and tokens cross the network in clear text. Use mqtts:// or wss:// outside the lab.`);
  }

  if (Object.keys(errors).length > 0) return { ok: false, errors, warnings };
  return { ok: true, value: { ...f, host, uuid, rawPsk }, warnings };
}
