/*
 * AEDI - IONITY GLOBAL | @ionity/prov - ionity-prov/2 | board.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * One normalised view of a board, whether it runs fw 2.x (prov/1) or fw 3
 * (prov/2). The UI never branches on raw JSON shapes.
 *
 * NAMING TRAP: in prov/1, "mcp_url" is the board's OWN LAN MCP endpoint
 * (http://<board-ip>/mcp). The MCP host the board connects TO is "host_url"
 * in prov/2 and "server"/"mqtt_port" in prov/1. Never mix them up.
 */
import { PROV_V1_LINE_MAX, PROV_V2_LINE_MAX, type ProvVersion } from './framing.ts';
import { V1_SET_FIELDS, type SetFieldName } from './fields.ts';
import { fromV1Server } from './host-url.ts';
import { normalizeUuid } from './uuid.ts';

export interface BoardInfo {
  readonly prov: ProvVersion;
  readonly lineMax: number;
  readonly setFields: ReadonlySet<string>;
  /** "esp32-" + base MAC (fw 2.x device_id) */
  readonly hwid: string;
  readonly fw?: string;
  readonly product?: string;
  readonly chip?: string;
  readonly chipRev?: number;
  readonly flashMb?: number;
  readonly mac?: string;
  readonly provisioned: boolean;
  readonly ssid?: string;
  readonly hasPass: boolean;
  /** where the board connects; "" = mDNS discovery */
  readonly hostUrl: string;
  readonly deviceUuid?: string;
  readonly hasCa: boolean;
  readonly hasMqttPass: boolean;
  readonly hasMcpToken: boolean;
  readonly role?: string;
  readonly site?: string;
  readonly group?: string;
  readonly label?: string;
  readonly mode?: string;
  readonly wifi: 'connected' | 'down' | 'unknown';
  readonly wifiError?: string;
  readonly ip?: string;
  readonly rssi?: number;
  /** broker / host session is up */
  readonly mqtt?: boolean;
  /** the board's own LAN MCP endpoint (prov/1 "mcp_url") */
  readonly boardMcpUrl?: string;
  readonly knownNetworks?: readonly string[];
}

export interface TestResult {
  readonly ok: boolean;
  readonly wifi: { readonly ok: boolean; readonly ip?: string; readonly rssi?: number; readonly error?: string };
  /** undefined when the board did not report it (standalone role, or old firmware) */
  readonly broker: { readonly ok: boolean | undefined; readonly detail?: string };
  readonly board?: BoardInfo;
}

export interface Network {
  readonly ssid: string;
  readonly rssi: number;
  readonly channel: number;
  readonly open: boolean;
}

type J = Readonly<Record<string, unknown>>;

const str = (j: J, k: string): string | undefined => (typeof j[k] === 'string' ? (j[k] as string) : undefined);
const num = (j: J, k: string): number | undefined => (typeof j[k] === 'number' && Number.isFinite(j[k]) ? (j[k] as number) : undefined);
const bool = (j: J, k: string): boolean | undefined => (typeof j[k] === 'boolean' ? (j[k] as boolean) : undefined);
const obj = (j: J, k: string): J | undefined => {
  const v = j[k];
  return typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as J) : undefined;
};

/** Spread helper for exactOptionalPropertyTypes: omit the key instead of writing undefined. */
function opt<K extends string, V>(key: K, value: V | undefined): { [P in K]?: V } {
  return (value === undefined ? {} : { [key]: value }) as { [P in K]?: V };
}

export function provVersionOf(j: J): ProvVersion {
  return num(j, 'prov') === 2 ? 2 : 1;
}

export function normalizeBoard(j: J): BoardInfo {
  const prov = provVersionOf(j);
  const lineMax = prov === 2 ? Math.max(PROV_V1_LINE_MAX, num(j, 'line_max') ?? PROV_V2_LINE_MAX) : PROV_V1_LINE_MAX;
  const advertised = Array.isArray(j['set_fields']) ? (j['set_fields'] as unknown[]).filter((x): x is string => typeof x === 'string') : null;
  const setFields: ReadonlySet<string> = prov === 2 && advertised ? new Set(advertised) : new Set<SetFieldName>(V1_SET_FIELDS);
  const wifiRaw = str(j, 'wifi');
  const uuid = str(j, 'device_uuid');
  const known = Array.isArray(j['known_networks'])
    ? (j['known_networks'] as unknown[]).map((x) => (typeof x === 'string' ? x : typeof x === 'object' && x !== null ? String((x as J)['ssid'] ?? '') : '')).filter(Boolean)
    : undefined;
  return {
    prov,
    lineMax,
    setFields,
    hwid: str(j, 'hwid') ?? str(j, 'device_id') ?? 'unknown',
    ...opt('fw', str(j, 'fw')),
    ...opt('product', str(j, 'product')),
    ...opt('chip', str(j, 'chip')),
    ...opt('chipRev', num(j, 'chip_rev')),
    ...opt('flashMb', num(j, 'flash_mb')),
    ...opt('mac', str(j, 'mac')),
    provisioned: bool(j, 'provisioned') ?? false,
    ...opt('ssid', str(j, 'ssid')),
    hasPass: bool(j, 'has_pass') ?? false,
    hostUrl: prov === 2 ? (str(j, 'host_url') ?? '') : fromV1Server(str(j, 'server') ?? '', num(j, 'mqtt_port')),
    ...opt('deviceUuid', uuid ? (normalizeUuid(uuid) ?? undefined) : undefined),
    hasCa: bool(j, 'has_ca') ?? false,
    hasMqttPass: bool(j, 'has_mqtt_pass') ?? false,
    hasMcpToken: bool(j, 'has_mcp_token') ?? false,
    ...opt('role', str(j, 'role')),
    ...opt('site', str(j, 'site')),
    ...opt('group', str(j, 'group')),
    ...opt('label', str(j, 'label')),
    ...opt('mode', str(j, 'mode')),
    wifi: wifiRaw === 'connected' ? 'connected' : wifiRaw === 'down' ? 'down' : 'unknown',
    ...opt('wifiError', str(j, 'wifi_error')),
    ...opt('ip', str(j, 'ip')),
    ...opt('rssi', num(j, 'rssi')),
    ...opt('mqtt', bool(j, 'mqtt')),
    ...opt('boardMcpUrl', str(j, 'mcp_url') || undefined),
    ...opt('knownNetworks', known),
  };
}

/**
 * prov/1 test reply: {"op":"test","ok":true,...hello fields...,"mqtt":true} or {"ok":false,"error":"..."}
 * prov/2 test reply: {"op":"test","ok":..,"wifi":{ok,ip,rssi,error},"broker":{ok,detail},...}
 */
export function normalizeTest(j: J): TestResult {
  const ok = bool(j, 'ok') ?? false;
  const w = obj(j, 'wifi');
  const b = obj(j, 'broker');
  if (w || b) {
    return {
      ok,
      wifi: {
        ok: (w && bool(w, 'ok')) ?? false,
        ...opt('ip', w && str(w, 'ip')),
        ...opt('rssi', w && num(w, 'rssi')),
        ...opt('error', w && str(w, 'error')),
      },
      broker: { ok: b ? bool(b, 'ok') : undefined, ...opt('detail', b && str(b, 'detail')) },
      ...opt('board', obj(j, 'board') ? normalizeBoard(obj(j, 'board') as J) : undefined),
    };
  }
  if (!ok) return { ok, wifi: { ok: false, ...opt('error', str(j, 'error')) }, broker: { ok: undefined } };
  const board = normalizeBoard(j);
  return {
    ok,
    wifi: { ok: true, ...opt('ip', board.ip), ...opt('rssi', board.rssi) },
    broker: { ok: bool(j, 'mqtt'), ...opt('detail', str(j, 'host_resolved') ? `host ${str(j, 'host_resolved')} via ${str(j, 'host_via') ?? '?'}` : undefined) },
    board,
  };
}

export function normalizeNetworks(j: J): Network[] {
  const arr = Array.isArray(j['networks']) ? (j['networks'] as unknown[]) : [];
  const out: Network[] = [];
  for (const n of arr) {
    if (typeof n !== 'object' || n === null) continue;
    const r = n as J;
    const ssid = str(r, 'ssid');
    if (!ssid) continue; // hidden networks report an empty SSID
    out.push({ ssid, rssi: num(r, 'rssi') ?? -100, channel: num(r, 'ch') ?? 0, open: bool(r, 'open') ?? false });
  }
  // strongest first, one entry per SSID (mesh / dual-AP setups repeat the name)
  out.sort((a, b) => b.rssi - a.rssi);
  return out.filter((n, i) => out.findIndex((m) => m.ssid === n.ssid) === i);
}
