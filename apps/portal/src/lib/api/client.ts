/*
 * AEDI - IONITY GLOBAL | @ionity/portal | api/client.ts | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
 *
 * Typed client for the fleet host REST API (services/fleet-host, 3.x).
 * The admin token is passed in by the caller and lives in React state only:
 * it is never written to storage (see store.ts) and never put in a URL.
 */

export interface FleetDevice {
  readonly device_id: string;
  readonly site: string;
  readonly group: string;
  readonly label: string | null;
  readonly fw: string | null;
  readonly ip: string | null;
  readonly transport: string;
  readonly health: 'online' | 'stale' | 'offline' | 'alerting';
  readonly last_seen: number | null;
  readonly last_seen_age_s: number | null;
  readonly uptime_s: number | null;
  readonly metrics: Readonly<Record<string, number>>;
  readonly active_alerts: readonly string[];
  readonly protocol: 'json' | 'ewp1';
  readonly kind: 'esp32' | 'pi' | 'pico' | 'other';
  readonly mac: string | null;
  readonly chip: string | null;
  readonly tools: readonly string[];
  readonly dns: Readonly<Record<string, number>> | null;
  readonly watchlist_version: number | null;
}

export interface DnsQuery {
  readonly ts: number;
  readonly client_ip: string;
  readonly mac: string | null;
  readonly hostname: string | null;
  readonly label: string | null;
  readonly node_id: string | null;
  readonly qname: string;
  readonly qtype: number | string;
  readonly rcode: number | null;
  readonly cached: boolean | number | null;
  readonly latency_ms: number | null;
  readonly count: number | null;
  readonly category: string | null;
  readonly flagged: boolean | number | null;
  readonly rule: string | null;
}

export interface FlagEvent {
  readonly id: number;
  readonly ts?: number;
  readonly first_ts?: number;
  readonly last_ts?: number;
  readonly client_ip: string;
  readonly mac: string | null;
  readonly hostname: string | null;
  readonly label: string | null;
  readonly node_id: string | null;
  readonly qname: string;
  readonly rule: string;
  readonly category: string;
  readonly hits: number;
  readonly new?: boolean;
}

export interface WatchCategory {
  readonly id: number;
  readonly name: string;
  readonly label: string;
  readonly builtin: boolean;
  readonly rules?: number;
}

export interface WatchRule {
  readonly id: number;
  readonly pattern: string;
  readonly category: string;
  readonly category_label?: string;
  readonly alert: boolean;
  readonly enabled: boolean;
  readonly builtin: boolean;
  readonly hits?: number;
}

export interface Watchlist {
  readonly version: number;
  readonly categories: readonly WatchCategory[];
  readonly rules: readonly WatchRule[];
  readonly nodes: readonly { readonly device_id: string; readonly label: string | null; readonly version: number | null; readonly in_sync: boolean }[];
}

export interface DnsFilter {
  readonly domain?: string;
  readonly client_ip?: string;
  readonly mac?: string;
  readonly label?: string;
  readonly category?: string;
  readonly node_id?: string;
  readonly flagged_only?: boolean;
  readonly minutes?: number;
  readonly limit?: number;
  readonly offset?: number;
}

export interface ProvisioningDefaults {
  readonly server: string | null;
  readonly mdns_host: string;
  readonly mqtt_port: number;
  readonly http_port: number;
  readonly site: string;
  readonly group: string;
  readonly fleet_name?: string;
  readonly require_token: boolean;
  readonly admin: boolean;
  readonly warning: string | null;
  readonly fleet_token?: string;
  readonly mqtt_user?: string;
}

export interface LabelsView {
  readonly file: string;
  readonly error: string | null;
  readonly entries: readonly { readonly key: string; readonly kind?: string; readonly label: string }[];
  readonly discovered: readonly {
    readonly ip: string;
    readonly mac: string | null;
    readonly hostname: string | null;
    readonly vendor: string | null;
    readonly label: string | null;
    readonly last_seen: number | null;
    readonly node_id: string | null;
  }[];
}

export type NodeActionName =
  | 'ping'
  | 'identify'
  | 'reboot'
  | 'set_alert_led'
  | 'arp_scan'
  | 'flush_dns_cache'
  | 'sync_watchlist'
  | 'network_stats'
  | 'local_devices'
  | 'dns_log'
  | 'ping_target';

/** POST /api/v1/nodes/{id}/action {action: 'ping_target'} - EWP ping_host (0x47) run ON the node. */
export interface PingTargetResult {
  readonly ok: boolean;
  readonly error?: string;
  readonly target?: string;
  readonly reachable?: boolean;
  readonly loss_pct?: number | null;
  readonly result?: {
    readonly error: 'none' | 'resolve_failed' | 'timeout' | 'not_permitted' | 'busy' | number;
    readonly resolvedIp?: string;
    readonly sent: number;
    readonly received: number;
    readonly rttMinMs?: number;
    readonly rttAvgMs?: number;
    readonly rttMaxMs?: number;
  };
}

/** Bounds of EWP_LIMITS.ping* (packages/protocol) - checked before the request is sent. */
export const PING_BOUNDS = { countMax: 10, timeoutMin: 100, timeoutMax: 5000, intervalMin: 200, intervalMax: 5000 } as const;
const PING_TARGET_RE = /^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$/;

/** null when the target is usable, otherwise a sentence for the form. */
export function checkPingTarget(t: string): string | null {
  return PING_TARGET_RE.test(t.trim()) ? null : 'Enter a host name (example.com) or an IPv4 address (192.168.0.1).';
}

const PING_ERROR_TEXT: Record<string, string> = {
  resolve_failed: 'the name did not resolve on the board',
  timeout: 'no reply',
  not_permitted: 'the board may not send ICMP (Pi: check net.ipv4.ping_group_range)',
  busy: 'another ping is still running on this board',
};

/** One line for the card and the screen-reader announcement. */
export function pingSummary(r: PingTargetResult): string {
  if (!r.ok || !r.result) return r.error ?? 'failed';
  const x = r.result;
  const ip = x.resolvedIp && x.resolvedIp !== r.target ? ` (${x.resolvedIp})` : '';
  if (x.error !== 'none' && x.error !== 0) {
    return `${r.target ?? ''}${ip}: ${PING_ERROR_TEXT[String(x.error)] ?? String(x.error)}`;
  }
  return `${r.target ?? ''}${ip}: ${x.received}/${x.sent} replies, ${String(r.loss_pct ?? 0)} % loss, ` +
    `rtt ${String(x.rttMinMs)}/${String(x.rttAvgMs)}/${String(x.rttMaxMs)} ms (min/avg/max)`;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

export interface ClientOptions {
  readonly adminToken?: string;
  readonly fetchImpl?: typeof fetch;
  readonly timeoutMs?: number;
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === '' || v === false) continue;
    u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : '';
}

/** ws:// or wss:// URL for a path on the API base ('' = this page's origin). */
export function wsUrl(base: string, path: string, loc: Pick<Location, 'origin'> | undefined = globalThis.location): string {
  const origin = base || loc?.origin || 'http://localhost';
  const u = new URL(path, origin.endsWith('/') ? origin : `${origin}/`);
  // keep a path prefix in base (reverse proxies): new URL drops it for absolute paths
  const prefix = new URL(origin).pathname.replace(/\/+$/, '');
  u.pathname = prefix + path;
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
  return u.href;
}

export class FleetApi {
  private readonly fetchImpl: typeof fetch;
  readonly base: string;
  private readonly opts: ClientOptions;

  constructor(base: string, opts: ClientOptions = {}) {
    this.base = base;
    this.opts = opts;
    this.fetchImpl = opts.fetchImpl ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  }

  get isAdmin(): boolean {
    return Boolean(this.opts.adminToken);
  }

  withToken(adminToken: string): FleetApi {
    return new FleetApi(this.base, { ...this.opts, adminToken });
  }

  private async req<T>(method: string, path: string, body?: unknown, timeoutMs?: number): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (this.opts.adminToken) headers['Authorization'] = `Bearer ${this.opts.adminToken}`;
    let r: Response;
    try {
      r = await this.fetchImpl(`${this.base}${path}`, {
        method,
        headers,
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        signal: AbortSignal.timeout(timeoutMs ?? this.opts.timeoutMs ?? 15_000),
      });
    } catch (e) {
      const err = e as Error;
      throw new ApiError(0, err.name === 'TimeoutError' ? 'the fleet host did not answer in time' : `cannot reach the fleet host (${err.message})`);
    }
    const type = r.headers.get('content-type') ?? '';
    const data: unknown = type.includes('json') ? await r.json().catch(() => null) : null;
    if (!r.ok) {
      const detail = (data as { detail?: unknown } | null)?.detail;
      const msg =
        typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? detail.map((d: { msg?: string }) => d.msg ?? '').filter(Boolean).join('; ')
            : r.status === 401 || r.status === 403
              ? 'this needs the admin token'
              : `HTTP ${r.status}`;
      throw new ApiError(r.status, msg || `HTTP ${r.status}`);
    }
    if (!type.includes('json')) throw new ApiError(r.status, `${this.base || 'this page'} is not a fleet host API`);
    return data as T;
  }

  health() {
    return this.req<{ status: string; version?: string } & Record<string, unknown>>('GET', '/api/v1/health');
  }
  devices() {
    return this.req<{ devices: FleetDevice[] } | FleetDevice[]>('GET', '/api/v1/devices').then((d) => (Array.isArray(d) ? d : d.devices));
  }
  nodeAction(deviceId: string, action: NodeActionName, params: Record<string, unknown> = {}) {
    return this.req<Record<string, unknown> & { ok?: boolean; error?: string }>('POST', `/api/v1/nodes/${encodeURIComponent(deviceId)}/action`, { action, params });
  }
  /** ICMP from the node itself; resolves after about (count - 1) x interval + timeout. */
  pingFromNode(deviceId: string, target: string, opts: { count?: number; timeout_ms?: number; interval_ms?: number } = {}) {
    const count = opts.count ?? 4;
    const budgetMs = (count - 1) * (opts.interval_ms ?? 1000) + (opts.timeout_ms ?? 1000) + 5000;
    return this.req<PingTargetResult>(
      'POST',
      `/api/v1/nodes/${encodeURIComponent(deviceId)}/action`,
      { action: 'ping_target', params: { target: target.trim(), ...opts } },
      Math.max(budgetMs, this.opts.timeoutMs ?? 15_000),
    );
  }
  dnsQueries(f: DnsFilter = {}) {
    return this.req<{ queries: DnsQuery[]; has_more: boolean; next_offset: number | null }>('GET', `/api/v1/dns/queries${qs({ ...f })}`);
  }
  dnsFlagged(f: { minutes?: number; category?: string; client_ip?: string; label?: string; active_only?: boolean } = {}) {
    return this.req<{ flags: FlagEvent[] } & Record<string, unknown>>('GET', `/api/v1/dns/flagged${qs(f)}`);
  }
  watchlist() {
    return this.req<Watchlist>('GET', '/api/v1/watchlist');
  }
  addRule(pattern: string, category: string, alert: boolean) {
    return this.req<WatchRule>('POST', '/api/v1/watchlist/rules', { pattern, category, alert });
  }
  setRuleEnabled(rule: number | string, enabled: boolean) {
    return this.req<unknown>('PATCH', `/api/v1/watchlist/rules/${encodeURIComponent(String(rule))}`, { enabled });
  }
  deleteRule(rule: number | string) {
    return this.req<unknown>('DELETE', `/api/v1/watchlist/rules/${encodeURIComponent(String(rule))}`);
  }
  addCategory(name: string, label: string) {
    return this.req<unknown>('POST', '/api/v1/watchlist/categories', { name, label });
  }
  deleteCategory(name: string) {
    return this.req<unknown>('DELETE', `/api/v1/watchlist/categories/${encodeURIComponent(name)}`);
  }
  syncWatchlist() {
    return this.req<Record<string, unknown>>('POST', '/api/v1/watchlist/sync');
  }
  labels() {
    return this.req<LabelsView>('GET', '/api/v1/labels');
  }
  setLabel(key: string, label: string) {
    return this.req<unknown>('PUT', `/api/v1/labels/${encodeURIComponent(key)}`, { label });
  }
  deleteLabel(key: string) {
    return this.req<unknown>('DELETE', `/api/v1/labels/${encodeURIComponent(key)}`);
  }
  provisioningDefaults() {
    return this.req<ProvisioningDefaults>('GET', '/api/v1/provisioning/defaults');
  }
  /** Firmware manifest URL on this host; images resolve relative to it (/api/v1/firmware/<file>). */
  firmwareManifestUrl(): string {
    return `${this.base || globalThis.location?.origin || ''}/api/v1/firmware/manifest`;
  }
}

// ---- small view helpers (pure, tested) ------------------------------------------------

/** First metric present among aliases (EWP/1 names first, firmware 2.x JSON names after). */
export function metric(d: Pick<FleetDevice, 'metrics'>, ...keys: string[]): number | null {
  for (const k of keys) {
    const v = d.metrics[k];
    if (typeof v === 'number' && Number.isFinite(v)) return v;
  }
  return null;
}

export function fmtUptime(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return '-';
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m ${Math.floor(s % 60)}s`;
}

export function fmtBytes(n: number | null): string {
  if (n === null) return '-';
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} kB`;
  return `${n} B`;
}

const QTYPES: Record<number, string> = { 1: 'A', 2: 'NS', 5: 'CNAME', 6: 'SOA', 12: 'PTR', 15: 'MX', 16: 'TXT', 28: 'AAAA', 33: 'SRV', 64: 'SVCB', 65: 'HTTPS' };
export function qtypeName(t: number | string): string {
  return typeof t === 'number' ? (QTYPES[t] ?? `TYPE${t}`) : t;
}

/** Who made a query, as a person would name it. */
export function clientName(q: Pick<DnsQuery, 'label' | 'hostname' | 'client_ip'>): string {
  return q.label || q.hostname || q.client_ip;
}

/** Watchlist versions are a u32 content hash: shown as 8 hex digits, as the host logs them. */
export function fmtVersion(v: number | null | undefined): string {
  return v === null || v === undefined ? '?' : (v >>> 0).toString(16).padStart(8, '0');
}

/** What the host's provisioning defaults pre-fill in the form (never overrides what a board or a person set). */
export function defaultsToForm(d: ProvisioningDefaults): {
  hostUrl?: string;
  site?: string;
  group?: string;
  fleetToken?: string;
  mqttUser?: string;
} {
  const out: { hostUrl?: string; site?: string; group?: string; fleetToken?: string; mqttUser?: string } = {};
  if (d.server) out.hostUrl = `mqtt://${d.server.includes(':') && !d.server.startsWith('[') ? `[${d.server}]` : d.server}:${d.mqtt_port || 1883}`;
  if (d.site) out.site = d.site;
  if (d.group) out.group = d.group;
  if (d.fleet_token) out.fleetToken = d.fleet_token;
  if (d.mqtt_user) out.mqttUser = d.mqtt_user;
  return out;
}
