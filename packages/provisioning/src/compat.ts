/*
 * AEDI - IONITY GLOBAL | @ionity/prov - ionity-prov/2 | compat.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Turn a valid form into the `set` fields THIS board understands.
 * fw 2.x (prov/1) and fw 3 (prov/2) are provisioned by the same portal; what
 * an older board cannot store is reported, never sent and never silently lost.
 */
import type { BoardInfo } from './board.ts';
import type { SetFieldName, SetFields } from './fields.ts';
import { formatRequest } from './framing.ts';
import { toV1Server } from './host-url.ts';
import type { FieldErrors, ValidForm } from './schema.ts';
import { utf8ByteLength } from './text.ts';

export interface SetPlan {
  readonly fields: SetFields;
  readonly errors: FieldErrors;
  readonly warnings: readonly string[];
  /** problem with the request as a whole (not one field) */
  readonly formError?: string;
  /** bytes of the request line as it will be written (prov/2 envelope + CRC included) */
  readonly requestBytes: number;
}

/** Room for "v":2,"id":4294967295 and the " *xxxxxxxx" suffix. */
const V2_ENVELOPE_SLACK = 40;

export function planSet(form: ValidForm, board: BoardInfo): SetPlan {
  const fields: SetFields = {};
  const errors: FieldErrors = {};
  const warnings: string[] = [];
  const v2 = board.prov === 2;
  const supports = (name: SetFieldName): boolean => board.setFields.has(name);

  // Wi-Fi
  fields.ssid = form.ssid;
  if (form.openNetwork) fields.pass = '';
  else if (!(form.keepPass && form.pass === '')) {
    if (form.rawPsk && !v2) errors.pass = 'Firmware 2.x accepts 8-63 character passwords only (no 64-hex PSK).';
    else fields.pass = form.pass;
  }

  // Host
  if (v2) {
    fields.host_url = form.host?.href ?? '';
  } else if (form.host) {
    const r = toV1Server(form.host);
    if (r.ok) {
      fields.server = r.server;
      if (r.mqtt_port !== undefined) fields.mqtt_port = r.mqtt_port;
      if (r.http_port !== undefined) fields.http_port = r.http_port;
    } else {
      errors.hostUrl = r.error;
    }
  } else {
    fields.server = ''; // fw 2.x: empty server = mDNS ionity-fleet.local
  }

  // Identity + TLS material (prov/2 only)
  if (form.uuid !== '' && form.uuid !== board.deviceUuid) {
    if (v2 && supports('device_uuid')) fields.device_uuid = form.uuid;
    else warnings.push('This board runs firmware 2.x, which cannot store a device UUID. It will be written when the board gets firmware 3.');
  }
  if (form.caPem.trim() !== '') {
    if (v2 && supports('ca_pem')) fields.ca_pem = form.caPem.trim() + '\n';
    else errors.caPem = 'This firmware cannot store a CA certificate. Install firmware 3 first.';
  }

  // Fleet metadata + secrets: empty = unchanged
  fields.role = form.role;
  if (form.site !== '') fields.site = form.site;
  if (form.group !== '') fields.group = form.group;
  if (form.label !== '') fields.label = form.label;
  if (form.mqttUser !== '') fields.mqtt_user = form.mqttUser;
  if (form.mqttPass !== '') fields.mqtt_pass = form.mqttPass;
  if (form.mcpToken !== '') fields.mcp_token = form.mcpToken;
  if (form.fleetToken !== '') fields.fleet_token = form.fleetToken;

  // Never send a field the board did not advertise (prov/2) or does not know (prov/1).
  for (const name of Object.keys(fields) as SetFieldName[]) {
    if (!supports(name)) {
      delete fields[name];
      warnings.push(`The board's firmware does not accept "${name}"; it was left out.`);
    }
  }

  const line = formatRequest({ op: 'set', ...fields }, { crc: v2 });
  const requestBytes = utf8ByteLength(line.trimEnd()) + (v2 ? V2_ENVELOPE_SLACK : 0);
  if (requestBytes > board.lineMax) {
    const formError = `These settings are ${requestBytes} bytes; this firmware reads at most ${board.lineMax} per line. Shorten the label or tokens, or update the firmware.`;
    return { fields, errors, warnings, formError, requestBytes };
  }
  return { fields, errors, warnings, requestBytes };
}
