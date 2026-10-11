/*
 * AEDI - IONITY GLOBAL | @ionity/prov - ionity-prov/2 | host-url.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The "MCP host URL" a board connects to. One string says transport, host
 * and port, so a board can be moved from the lab broker to the fleet broker
 * (or to WebSocket) without new firmware:
 *
 *   typed                         stored on the board (canonical href)
 *   192.168.0.3                   mqtt://192.168.0.3:1883        lab Mosquitto
 *   mqtts://fleet.ionity.lan      mqtts://fleet.ionity.lan:8883  TLS (CA: built-in bundle or ca_pem)
 *   wss://gw.ionity.lan/ewp       wss://gw.ionity.lan:443/ewp    EWP over WebSocket
 *   http://192.168.0.3            http://192.168.0.3:8099        fw 2.x HTTP fallback / legacy fleet host
 *   (empty)                       ""                             mDNS discovery of ionity-fleet.local
 *
 * The canonical href ALWAYS carries the port. The firmware then needs no
 * default-port table, and "http" here defaults to the fleet host's 8099 - a
 * standard URL parser on the board would otherwise read http://host as port 80.
 *
 * A bare host ("192.168.0.3") is read as mqtt:// - what the old flasher's
 * "server" box meant. Credentials in the URL are refused: they would sit in
 * the URL string on screen and in logs; use mqtt_user / mqtt_pass instead.
 */
import { utf8ByteLength } from './text.ts';
import { LIMITS } from './fields.ts';

export type HostScheme = 'mqtt' | 'mqtts' | 'ws' | 'wss' | 'http';

/** Ports assumed when the person leaves the port out. http = fleet-host 2.x API port, not 80. */
export const DEFAULT_PORT = { mqtt: 1883, mqtts: 8883, ws: 80, wss: 443, http: 8099 } as const satisfies Record<HostScheme, number>;

export interface HostUrl {
  readonly scheme: HostScheme;
  /** lowercase DNS name, IPv4 literal, or [IPv6] literal (brackets kept) */
  readonly host: string;
  readonly port: number;
  /** "" except for ws/wss */
  readonly path: string;
  /** canonical string the board stores: scheme://host:port[/path], port always explicit */
  readonly href: string;
  readonly tls: boolean;
}

export type HostUrlResult = { readonly ok: true; readonly value: HostUrl } | { readonly ok: false; readonly error: string };

const URL_RE = /^(?<scheme>[a-z][a-z0-9+.-]*):\/\/(?<auth>[^@/?#]*@)?(?<host>\[[0-9a-f:.]+\]|[^:/?#\s]+)(?::(?<port>\d{1,5}))?(?<path>\/[^?#\s]*)?(?<rest>[?#].*)?$/i;
const IPV4_RE = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const LABEL_RE = /^(?!-)[a-z0-9-]{1,63}(?<!-)$/;

function validHostname(h: string): boolean {
  if (h.length > 253) return false;
  const labels = h.split('.');
  if (labels.every((l) => /^\d+$/.test(l))) return IPV4_RE.test(h); // all-numeric must be a real IPv4
  return labels.every((l) => LABEL_RE.test(l));
}

function isScheme(s: string): s is HostScheme {
  return s === 'mqtt' || s === 'mqtts' || s === 'ws' || s === 'wss' || s === 'http';
}

export function parseHostUrl(input: string): HostUrlResult {
  const raw = input.trim();
  if (raw === '') return { ok: false, error: 'empty' };
  if (utf8ByteLength(raw) > LIMITS.hostUrlBytes) return { ok: false, error: `longer than ${LIMITS.hostUrlBytes} bytes` };
  const withScheme = raw.includes('://') ? raw : `mqtt://${raw}`;
  const m = URL_RE.exec(withScheme);
  const g = m?.groups;
  if (!g || g['scheme'] === undefined || g['host'] === undefined) return { ok: false, error: 'not a URL like mqtt://host:1883' };
  const scheme = g['scheme'].toLowerCase();
  if (!isScheme(scheme)) return { ok: false, error: `unsupported scheme "${scheme}" (use mqtt, mqtts, ws, wss or http)` };
  if (g['auth']) return { ok: false, error: 'remove the user:password@ part - use the MQTT user and password fields' };
  if (g['rest']) return { ok: false, error: 'query strings and #fragments are not allowed' };

  const host = g['host'].toLowerCase();
  if (host.startsWith('[')) {
    if (!/^\[[0-9a-f:.]+\]$/.test(host) || !host.includes(':')) return { ok: false, error: 'invalid IPv6 literal' };
  } else if (!validHostname(host)) {
    return { ok: false, error: `"${host}" is not a valid host name or IPv4 address` };
  }

  let port: number = DEFAULT_PORT[scheme];
  if (g['port'] !== undefined) {
    port = Number(g['port']);
    if (!Number.isInteger(port) || port < 1 || port > 65_535) return { ok: false, error: 'port must be 1-65535' };
  }

  const path = g['path'] && g['path'] !== '/' ? g['path'] : '';
  if (path && scheme !== 'ws' && scheme !== 'wss') return { ok: false, error: `a path is only meaningful for ws:// and wss://` };

  const href = `${scheme}://${host}:${port}${path}`;
  return { ok: true, value: { scheme, host, port, path, href, tls: scheme === 'mqtts' || scheme === 'wss' } };
}

/**
 * fw 2.x (prov/1) has no host_url: it stores server + mqtt_port (or http_port)
 * and only speaks plain MQTT / HTTP over IPv4 or DNS/mDNS names.
 */
export function toV1Server(url: HostUrl): { ok: true; server: string; mqtt_port?: number; http_port?: number } | { ok: false; error: string } {
  if (url.host.startsWith('[')) return { ok: false, error: 'firmware 2.x cannot use an IPv6 host' };
  if (url.scheme === 'mqtt') return { ok: true, server: url.host, mqtt_port: url.port };
  if (url.scheme === 'http') return { ok: true, server: url.host, http_port: url.port };
  return { ok: false, error: `firmware 2.x only supports mqtt:// and http:// hosts - install firmware 3 to use ${url.scheme}://` };
}

/** A fw 2.x board's server + MQTT port as a canonical host URL (board card, form prefill). */
export function fromV1Server(server: string, mqttPort: number | undefined): string {
  if (!server) return '';
  return `mqtt://${server}:${mqttPort ?? DEFAULT_PORT.mqtt}`;
}
