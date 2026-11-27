/*
 * AEDI - IONITY GLOBAL | @ionity/portal | api/live.ts | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
 *
 * Live feeds from the fleet host:
 *   /ws/events  {type:"snapshot",dns,flags,watchlist_version} then {type:"batch",dns,flags,nodes}
 *   /ws/fleet   {type:"fleet_tick",summary,devices} every few seconds
 *
 * LiveSocket reconnects with capped exponential backoff + jitter and treats a
 * silent socket (no frame, not even a keepalive, for 70 s) as dead. The
 * reducers are pure so the views and the tests share them.
 */
import type { DnsQuery, FlagEvent, FleetDevice } from './client.ts';

export type SocketState = 'connecting' | 'open' | 'retrying' | 'closed';

export interface LiveSocketOptions {
  readonly onMessage: (msg: unknown) => void;
  readonly onState?: (s: SocketState, detail?: string) => void;
  readonly WebSocketImpl?: typeof WebSocket;
  readonly minDelayMs?: number;
  readonly maxDelayMs?: number;
  readonly silenceMs?: number;
  readonly random?: () => number;
}

export function backoffDelay(attempt: number, min = 500, max = 30_000, random: () => number = Math.random): number {
  const exp = Math.min(max, min * 2 ** Math.min(attempt, 16));
  return Math.round(exp / 2 + (random() * exp) / 2); // "equal jitter"
}

export class LiveSocket {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private watchdog: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  private readonly url: string;
  private readonly o: LiveSocketOptions;

  constructor(url: string, o: LiveSocketOptions) {
    this.url = url;
    this.o = o;
    this.connect();
  }

  private connect(): void {
    if (this.stopped) return;
    this.o.onState?.('connecting');
    const Impl = this.o.WebSocketImpl ?? WebSocket;
    let ws: WebSocket;
    try {
      ws = new Impl(this.url);
    } catch (e) {
      this.retry((e as Error).message);
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.o.onState?.('open');
      this.kick();
    };
    ws.onmessage = (ev: MessageEvent) => {
      this.kick();
      if (typeof ev.data !== 'string') return;
      let msg: unknown;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return; // one bad frame never kills the feed
      }
      this.o.onMessage(msg);
    };
    ws.onclose = (ev: CloseEvent) => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.retry(ev.reason || `closed (${ev.code})`);
    };
    ws.onerror = () => {
      /* onclose follows */
    };
  }

  private kick(): void {
    if (this.watchdog) clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => {
      // half-open TCP: the server sends a keepalive every 25-30 s
      const ws = this.ws;
      this.ws = null;
      try {
        ws?.close();
      } catch {
        /* already closed */
      }
      this.retry('no data from the host for a while');
    }, this.o.silenceMs ?? 70_000);
  }

  private retry(detail: string): void {
    if (this.watchdog) clearTimeout(this.watchdog);
    if (this.stopped) return;
    const delay = backoffDelay(this.attempt++, this.o.minDelayMs, this.o.maxDelayMs, this.o.random);
    this.o.onState?.('retrying', `${detail}; retrying in ${Math.ceil(delay / 1000)} s`);
    this.timer = setTimeout(() => this.connect(), delay);
  }

  close(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.watchdog) clearTimeout(this.watchdog);
    const ws = this.ws;
    this.ws = null;
    try {
      ws?.close();
    } catch {
      /* ignore */
    }
    this.o.onState?.('closed');
  }
}

// ---- DNS / flag feed reducer ----------------------------------------------------------

export const MAX_FEED = 500;

export interface NodeState {
  readonly device_id: string;
  readonly state: string;
  readonly at: number;
}

export interface LiveState {
  readonly dns: readonly DnsQuery[]; // newest first
  readonly flags: readonly FlagEvent[]; // newest first, one entry per episode id
  readonly nodes: Readonly<Record<string, NodeState>>;
  readonly watchlistVersion: number | null;
  readonly received: number;
}

export const EMPTY_LIVE: LiveState = { dns: [], flags: [], nodes: {}, watchlistVersion: null, received: 0 };

function isObj(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null && !Array.isArray(x);
}

function arr<T>(x: unknown): T[] {
  return Array.isArray(x) ? (x.filter(isObj) as T[]) : [];
}

function mergeFlags(existing: readonly FlagEvent[], incoming: readonly FlagEvent[]): FlagEvent[] {
  const byId = new Map<number, FlagEvent>();
  for (const f of existing) byId.set(f.id, f);
  for (const f of incoming) {
    const prev = byId.get(f.id);
    if (f.last_ts !== undefined) {
      byId.set(f.id, f); // a stored episode (snapshot): totals already summed
    } else if (prev) {
      // a live push carries the hits of THIS push: add them to the episode
      const last = f.ts ?? prev.last_ts;
      byId.set(f.id, { ...prev, qname: f.qname, hits: (prev.hits ?? 0) + (f.hits ?? 0), ...(last !== undefined ? { last_ts: last } : {}) });
    } else {
      byId.set(f.id, f.ts !== undefined ? { ...f, first_ts: f.ts, last_ts: f.ts } : f);
    }
  }
  return [...byId.values()].sort((a, b) => (b.last_ts ?? b.ts ?? 0) - (a.last_ts ?? a.ts ?? 0)).slice(0, 200);
}

export function liveReducer(s: LiveState, msg: unknown, now: number = Date.now() / 1000): LiveState {
  if (!isObj(msg)) return s;
  if (msg['type'] === 'snapshot') {
    const dns = arr<DnsQuery>(msg['dns']).sort((a, b) => b.ts - a.ts).slice(0, MAX_FEED);
    return {
      dns,
      flags: mergeFlags([], arr<FlagEvent>(msg['flags'])),
      nodes: s.nodes,
      watchlistVersion: typeof msg['watchlist_version'] === 'number' ? msg['watchlist_version'] : s.watchlistVersion,
      received: s.received + dns.length,
    };
  }
  if (msg['type'] === 'batch') {
    const add = arr<DnsQuery>(msg['dns']);
    const flags = arr<FlagEvent>(msg['flags']);
    const nodes = arr<{ device_id?: unknown; state?: unknown }>(msg['nodes']);
    if (!add.length && !flags.length && !nodes.length) return s;
    const nextNodes = { ...s.nodes };
    for (const n of nodes) {
      if (typeof n.device_id === 'string') nextNodes[n.device_id] = { device_id: n.device_id, state: String(n.state ?? ''), at: now };
    }
    return {
      dns: add.length ? [...add.reverse(), ...s.dns].slice(0, MAX_FEED) : s.dns,
      flags: flags.length ? mergeFlags(s.flags, flags) : s.flags,
      nodes: nextNodes,
      watchlistVersion: s.watchlistVersion,
      received: s.received + add.length,
    };
  }
  return s;
}

export interface FeedFilter {
  readonly device: string; // label / host name / IP / MAC / node id, substring
  readonly domain: string; // substring or *.suffix
  readonly flaggedOnly: boolean;
}

export const NO_FILTER: FeedFilter = { device: '', domain: '', flaggedOnly: false };

export function matchesDomain(qname: string, pattern: string): boolean {
  const p = pattern.trim().toLowerCase().replace(/\.$/, '');
  if (!p) return true;
  const q = qname.toLowerCase();
  if (p.startsWith('*.')) {
    const suf = p.slice(2);
    return q === suf || q.endsWith(`.${suf}`);
  }
  return q.includes(p);
}

export function filterFeed(dns: readonly DnsQuery[], f: FeedFilter): DnsQuery[] {
  const dev = f.device.trim().toLowerCase();
  return dns.filter((q) => {
    if (f.flaggedOnly && !q.flagged) return false;
    if (!matchesDomain(q.qname, f.domain)) return false;
    if (dev) {
      const hay = [q.label, q.hostname, q.client_ip, q.mac, q.node_id].filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(dev)) return false;
    }
    return true;
  });
}

/** Flags still "active": seen within the last `withinS` seconds. */
export function activeFlags(flags: readonly FlagEvent[], now: number = Date.now() / 1000, withinS = 600): FlagEvent[] {
  return flags.filter((f) => now - (f.last_ts ?? f.ts ?? 0) <= withinS);
}

// ---- fleet tick -----------------------------------------------------------------------

export interface FleetTick {
  readonly devices: readonly FleetDevice[];
  readonly summary: Readonly<Record<string, unknown>> | null;
  readonly truncated: boolean;
}

export function parseFleetTick(msg: unknown): FleetTick | null {
  if (!isObj(msg) || msg['type'] !== 'fleet_tick') return null;
  return { devices: arr<FleetDevice>(msg['devices']), summary: isObj(msg['summary']) ? msg['summary'] : null, truncated: msg['truncated'] === true };
}
