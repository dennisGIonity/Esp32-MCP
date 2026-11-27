/*
 * AEDI - IONITY GLOBAL | @ionity/portal | fleet API + live feed | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
import { describe, expect, it, vi } from 'vitest';
import { manifestCandidates } from '../src/features/flash/FlashPanel.tsx';
import { categoryText } from '../src/features/network/FlaggedBanner.tsx';
import { ApiError, FleetApi, checkPingTarget, defaultsToForm, fmtUptime, fmtVersion, metric, pingSummary, qtypeName, wsUrl, type DnsQuery, type FlagEvent } from '../src/lib/api/client.ts';
import { EMPTY_LIVE, LiveSocket, MAX_FEED, activeFlags, backoffDelay, filterFeed, liveReducer, matchesDomain, parseFleetTick } from '../src/lib/api/live.ts';
import { checkCategory, checkPattern } from '../src/lib/api/watchlist.ts';

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const q = (over: Partial<DnsQuery>): DnsQuery => ({
  ts: 1000,
  client_ip: '192.168.0.42',
  mac: 'aa:bb:cc:dd:ee:ff',
  hostname: null,
  label: 'Kitchen TV',
  node_id: 'esp32-98a316e5d18c',
  qname: 'www.youtube.com',
  qtype: 1,
  rcode: 0,
  cached: false,
  latency_ms: 12,
  count: 1,
  category: 'video_streaming:youtube',
  flagged: true,
  rule: '*.youtube.com',
  ...over,
});

describe('FleetApi', () => {
  it('sends the admin token as a bearer header and JSON bodies', async () => {
    const seen: { url: string; init: RequestInit }[] = [];
    const fetchImpl: typeof fetch = async (url, init) => {
      seen.push({ url: String(url), init: init ?? {} });
      return json(201, { id: 9, pattern: '*.tiktok.com' });
    };
    const api = new FleetApi('http://h:8099', { fetchImpl, adminToken: 's3cret' });
    await api.addRule('*.tiktok.com', 'video_streaming:tiktok', true);
    expect(seen[0]?.url).toBe('http://h:8099/api/v1/watchlist/rules');
    expect(seen[0]?.init.method).toBe('POST');
    expect((seen[0]?.init.headers as Record<string, string>)['Authorization']).toBe('Bearer s3cret');
    expect(JSON.parse(String(seen[0]?.init.body))).toEqual({ pattern: '*.tiktok.com', category: 'video_streaming:tiktok', alert: true });
  });

  it('builds DNS filters into the query string, dropping empty ones', async () => {
    let url = '';
    const api = new FleetApi('', {
      fetchImpl: async (u) => {
        url = String(u);
        return json(200, { queries: [], has_more: false, next_offset: null });
      },
    });
    await api.dnsQueries({ domain: '*.googlevideo.com', label: '', flagged_only: true, limit: 50 });
    expect(url).toBe('/api/v1/dns/queries?domain=*.googlevideo.com&flagged_only=true&limit=50');
  });

  it('surfaces the host error detail (FastAPI string and validation list)', async () => {
    const answers = [json(422, { detail: 'a rule needs at least two labels' }), json(422, { detail: [{ msg: 'field required' }] }), json(403, {})];
    const api = new FleetApi('http://h', { fetchImpl: async () => answers.shift() ?? json(500, {}) });
    await expect(api.addRule('com', 'x:y', true)).rejects.toThrow('a rule needs at least two labels');
    await expect(api.addRule('a.com', 'x:y', true)).rejects.toThrow('field required');
    const err = await api.syncWatchlist().catch((x: unknown) => x);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(403);
    expect((err as ApiError).message).toBe('this needs the admin token');
  });

  it('refuses a non-JSON answer (a static server is not a fleet host)', async () => {
    const api = new FleetApi('http://h', { fetchImpl: async () => new Response('<html>', { status: 200, headers: { 'Content-Type': 'text/html' } }) });
    await expect(api.watchlist()).rejects.toThrow('is not a fleet host API');
  });

  it('accepts both device list shapes', async () => {
    const api = new FleetApi('http://h', { fetchImpl: async () => json(200, { count: 1, devices: [{ device_id: 'a' }] }) });
    expect((await api.devices()).map((d) => d.device_id)).toEqual(['a']);
  });

  it('points WebSockets at the same host, keeping a path prefix', () => {
    expect(wsUrl('http://192.168.0.3:8099', '/ws/events')).toBe('ws://192.168.0.3:8099/ws/events');
    expect(wsUrl('https://gw.ionity.lan/fleet', '/ws/fleet')).toBe('wss://gw.ionity.lan/fleet/ws/fleet');
    expect(wsUrl('', '/ws/events', { origin: 'https://h.lan' })).toBe('wss://h.lan/ws/events');
  });

  it('maps provisioning defaults onto the form', () => {
    expect(defaultsToForm({ server: '192.168.0.3', mdns_host: 'x', mqtt_port: 1883, http_port: 8099, site: 'lab', group: 'bench', require_token: false, admin: false, warning: null })).toEqual({
      hostUrl: 'mqtt://192.168.0.3:1883',
      site: 'lab',
      group: 'bench',
    });
    expect(defaultsToForm({ server: 'fd00::3', mdns_host: 'x', mqtt_port: 8883, http_port: 8099, site: '', group: '', require_token: true, admin: true, warning: null, fleet_token: 'tok', mqtt_user: 'ionity' })).toEqual({
      hostUrl: 'mqtt://[fd00::3]:8883',
      fleetToken: 'tok',
      mqttUser: 'ionity',
    });
  });
});

describe('view helpers', () => {
  it('reads EWP/1 metric names first, then firmware 2.x names', () => {
    expect(metric({ metrics: { rssi: -70, rssi_dbm: -61 } }, 'rssi_dbm', 'rssi')).toBe(-61);
    expect(metric({ metrics: { rssi: -70 } }, 'rssi_dbm', 'rssi')).toBe(-70);
    expect(metric({ metrics: {} }, 'rssi_dbm')).toBeNull();
  });
  it('formats uptime, versions, record types and categories', () => {
    expect(fmtUptime(59)).toBe('0m 59s');
    expect(fmtUptime(3 * 3600 + 120)).toBe('3h 2m');
    expect(fmtUptime(2 * 86400 + 7200)).toBe('2d 2h');
    expect(fmtUptime(null)).toBe('-');
    expect(fmtVersion(0xdeadbeef)).toBe('deadbeef');
    expect(fmtVersion(1)).toBe('00000001');
    expect(qtypeName(28)).toBe('AAAA');
    expect(qtypeName(65)).toBe('HTTPS');
    expect(categoryText('video_streaming:youtube')).toBe('YouTube (video streaming)');
    expect(categoryText('custom:env')).toBe('env (custom)');
  });
  it('orders firmware manifest sources: preference, fleet host, bundled; no duplicates', () => {
    expect(manifestCandidates(null, 'http://h/api/v1/firmware/manifest', 'http://p/firmware/manifest.json')).toEqual(['http://h/api/v1/firmware/manifest', 'http://p/firmware/manifest.json']);
    expect(manifestCandidates('http://x/m.json', 'http://x/m.json', 'b')).toEqual(['http://x/m.json', 'b']);
  });
});

describe('watchlist input checks (mirror the host rule engine)', () => {
  it('accepts domains and leading wildcards', () => {
    expect(checkPattern(' WWW.YouTube.com. ')).toEqual({ ok: true, value: 'www.youtube.com' });
    expect(checkPattern('*.googlevideo.com')).toEqual({ ok: true, value: '*.googlevideo.com' });
  });
  it('refuses URLs, bare TLDs and inner wildcards', () => {
    expect(checkPattern('https://youtube.com/watch').ok).toBe(false);
    expect(checkPattern('com').ok).toBe(false);
    expect(checkPattern('a.*.com').ok).toBe(false);
    expect(checkPattern('bad_-.com').ok).toBe(false);
  });
  it('checks category names', () => {
    expect(checkCategory('social:instagram')).toEqual({ ok: true, value: 'social:instagram' });
    expect(checkCategory('Social Media').ok).toBe(false);
  });
});

describe('live feed reducer', () => {
  it('starts from the snapshot and prepends batches, newest first, capped', () => {
    let s = liveReducer(EMPTY_LIVE, { type: 'snapshot', dns: [q({ ts: 1 }), q({ ts: 3 })], flags: [], watchlist_version: 7 });
    expect(s.dns.map((x) => x.ts)).toEqual([3, 1]);
    expect(s.watchlistVersion).toBe(7);
    s = liveReducer(s, { type: 'batch', dns: [q({ ts: 4 }), q({ ts: 5 })], flags: [], nodes: [] });
    expect(s.dns.map((x) => x.ts)).toEqual([5, 4, 3, 1]);
    const many = Array.from({ length: MAX_FEED + 50 }, (_, i) => q({ ts: 10 + i }));
    s = liveReducer(s, { type: 'batch', dns: many, flags: [], nodes: [] });
    expect(s.dns).toHaveLength(MAX_FEED);
    expect(s.dns[0]?.ts).toBe(10 + MAX_FEED + 49);
  });

  it('adds live flag hits to the stored episode instead of double counting', () => {
    const stored: FlagEvent = { id: 5, first_ts: 100, last_ts: 110, client_ip: '192.168.0.42', mac: null, hostname: null, label: 'Kitchen TV', node_id: null, qname: 'www.youtube.com', rule: '*.youtube.com', category: 'video_streaming:youtube', hits: 3 };
    let s = liveReducer(EMPTY_LIVE, { type: 'snapshot', dns: [], flags: [stored] });
    s = liveReducer(s, { type: 'batch', dns: [], nodes: [], flags: [{ id: 5, client_ip: '192.168.0.42', mac: null, hostname: null, label: 'Kitchen TV', node_id: null, rule: '*.youtube.com', category: 'video_streaming:youtube', ts: 120, hits: 2, new: false, qname: 'rr1---sn-abc.googlevideo.com' }] });
    expect(s.flags).toHaveLength(1);
    expect(s.flags[0]).toMatchObject({ id: 5, hits: 5, first_ts: 100, last_ts: 120, qname: 'rr1---sn-abc.googlevideo.com' });
    s = liveReducer(s, { type: 'batch', dns: [], nodes: [], flags: [{ id: 6, ts: 130, hits: 1, new: true, client_ip: '192.168.0.9', mac: null, hostname: null, label: null, node_id: null, qname: 'tiktok.com', rule: 'tiktok.com', category: 'video_streaming:tiktok' }] });
    expect(s.flags.map((f) => f.id)).toEqual([6, 5]);
    expect(activeFlags(s.flags, 135, 10).map((f) => f.id)).toEqual([6]);
  });

  it('records node state changes and ignores junk', () => {
    let s = liveReducer(EMPTY_LIVE, { type: 'batch', dns: [], flags: [], nodes: [{ device_id: 'esp32-1', state: 'offline' }] }, 50);
    expect(s.nodes['esp32-1']).toEqual({ device_id: 'esp32-1', state: 'offline', at: 50 });
    const same = liveReducer(s, { type: 'keepalive' });
    expect(same).toBe(s);
    s = liveReducer(s, 'garbage');
    expect(s).toBe(same);
  });

  it('filters by device, domain pattern and flagged', () => {
    const rows = [q({ qname: 'rr1---sn-abc.googlevideo.com' }), q({ qname: 'example.org', flagged: false, category: null, label: 'Laptop', client_ip: '192.168.0.7', mac: null })];
    expect(filterFeed(rows, { device: 'kitchen', domain: '', flaggedOnly: false })).toHaveLength(1);
    expect(filterFeed(rows, { device: '192.168.0.7', domain: '', flaggedOnly: false })[0]?.label).toBe('Laptop');
    expect(filterFeed(rows, { device: '', domain: '*.googlevideo.com', flaggedOnly: false })).toHaveLength(1);
    expect(filterFeed(rows, { device: '', domain: '', flaggedOnly: true })).toHaveLength(1);
    expect(matchesDomain('googlevideo.com', '*.googlevideo.com')).toBe(true);
    expect(matchesDomain('notgooglevideo.com', '*.googlevideo.com')).toBe(false);
  });

  it('parses fleet ticks', () => {
    expect(parseFleetTick({ type: 'keepalive' })).toBeNull();
    expect(parseFleetTick({ type: 'fleet_tick', devices: [{ device_id: 'a' }, 3], summary: {} })?.devices).toHaveLength(1);
  });
});

describe('LiveSocket', () => {
  class FakeWs {
    static all: FakeWs[] = [];
    onopen: (() => void) | null = null;
    onmessage: ((e: { data: unknown }) => void) | null = null;
    onclose: ((e: { code: number; reason: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    closed = false;
    readonly url: string;
    constructor(url: string) {
      this.url = url;
      FakeWs.all.push(this);
    }
    close() {
      this.closed = true;
    }
  }

  it('backs off with jitter and resets after a successful open', () => {
    expect(backoffDelay(0, 500, 30_000, () => 0)).toBe(250);
    expect(backoffDelay(0, 500, 30_000, () => 1)).toBe(500);
    expect(backoffDelay(10, 500, 30_000, () => 1)).toBe(30_000);
  });

  it('reconnects after a close and after silence, delivers parsed frames, stops on close()', () => {
    vi.useFakeTimers();
    FakeWs.all = [];
    const got: unknown[] = [];
    const states: string[] = [];
    const s = new LiveSocket('ws://h/ws/events', {
      WebSocketImpl: FakeWs as unknown as typeof WebSocket,
      onMessage: (m) => got.push(m),
      onState: (st) => states.push(st),
      minDelayMs: 100,
      silenceMs: 1000,
      random: () => 1,
    });
    const a = FakeWs.all[0]!;
    a.onopen?.();
    a.onmessage?.({ data: '{"type":"batch","dns":[]}' });
    a.onmessage?.({ data: 'not json' });
    expect(got).toEqual([{ type: 'batch', dns: [] }]);
    a.onclose?.({ code: 1006, reason: '' });
    vi.advanceTimersByTime(100);
    expect(FakeWs.all).toHaveLength(2);
    const b = FakeWs.all[1]!;
    b.onopen?.();
    vi.advanceTimersByTime(1000); // silent: watchdog closes and retries
    expect(b.closed).toBe(true);
    vi.advanceTimersByTime(100);
    expect(FakeWs.all).toHaveLength(3);
    s.close();
    vi.advanceTimersByTime(60_000);
    expect(FakeWs.all).toHaveLength(3);
    expect(states).toContain('retrying');
    expect(states.at(-1)).toBe('closed');
    vi.useRealTimers();
  });
});

describe('ping_target (EWP ping_host from the board)', () => {
  it('posts ping_target with a timeout long enough for the session', async () => {
    let body: unknown = null;
    let signal: AbortSignal | undefined;
    const fetchImpl: typeof fetch = async (_url, init) => {
      body = JSON.parse(String(init?.body));
      signal = init?.signal ?? undefined;
      return json(200, {
        ok: true, target: 'youtube.com', reachable: true, loss_pct: 25,
        result: { error: 'none', resolvedIp: '142.250.1.1', sent: 4, received: 3, rttMinMs: 9, rttAvgMs: 12, rttMaxMs: 20 },
      });
    };
    const api = new FleetApi('', { fetchImpl });
    const r = await api.pingFromNode('esp32-98a316e5d18c', ' youtube.com ', { count: 4 });
    expect(body).toEqual({ action: 'ping_target', params: { target: 'youtube.com', count: 4 } });
    expect(signal).toBeDefined();
    expect(pingSummary(r)).toBe('youtube.com (142.250.1.1): 3/4 replies, 25 % loss, rtt 9/12/20 ms (min/avg/max)');
  });

  it('explains node-side errors and validates targets before sending', () => {
    expect(pingSummary({ ok: true, target: 'nowhere.example', result: { error: 'resolve_failed', sent: 0, received: 0 } }))
      .toBe('nowhere.example: the name did not resolve on the board');
    expect(pingSummary({ ok: false, error: '429 too many requests' })).toBe('429 too many requests');
    expect(checkPingTarget('192.168.0.1')).toBeNull();
    expect(checkPingTarget('router.lan')).toBeNull();
    for (const bad of ['', 'bad host', 'x;reboot', '-x.com', 'a'.repeat(254)]) expect(checkPingTarget(bad)).not.toBeNull();
  });
});
