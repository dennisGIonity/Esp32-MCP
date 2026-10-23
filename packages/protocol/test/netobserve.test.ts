/*
 * AEDI - IONITY GLOBAL | @ionity/ewp | NetObserve contract runtime checks | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 * Run: node --experimental-strip-types --test test/netobserve.test.ts
 */
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { encode, decode } from 'cbor-x';
import * as ewp from '../src/index.ts';

test('hwid accepts ESP32 MACs and Pi serials, nothing else', () => {
  assert.equal(ewp.hwid('esp32-98a316e5d18c'), 'esp32-98a316e5d18c');
  assert.equal(ewp.hwid('pi-10000000a1b2c3d4'), 'pi-10000000a1b2c3d4');
  assert.equal(ewp.hwid('pi-88a29e27a18f'), 'pi-88a29e27a18f');
  for (const bad of ['pi-', 'pi-XYZ12345', 'esp32-98a316e5d18', 'pico-c18354253344139d', 'pi-1/2']) {
    assert.throws(() => ewp.hwid(bad), RangeError, bad);
  }
});

test('every CBOR key table uses unique one-byte keys', () => {
  const tables = Object.entries(ewp).filter(([n, v]) => n.endsWith('Key') && typeof v === 'object' && v !== null);
  assert.ok(tables.length > 30);
  for (const [name, t] of tables) {
    const keys = Object.values(t as Record<string, number>);
    assert.equal(new Set(keys).size, keys.length, `${name}: duplicate key`);
    for (const k of keys) assert.ok(Number.isInteger(k) && k >= 0 && k <= 23, `${name}: key ${k}`);
  }
});

test('NetObserve tool ids sit in the reserved range and have names', () => {
  const [lo, hi] = ewp.TOOL_ID_RANGE.netObserve;
  for (const id of [ewp.ToolId.SET_WATCHLIST, ewp.ToolId.GET_WATCHLIST, ewp.ToolId.GET_NETWORK_STATS,
    ewp.ToolId.GET_LOCAL_DEVICES, ewp.ToolId.QUERY_DNS_LOG, ewp.ToolId.SCAN_LAN, ewp.ToolId.FLUSH_DNS_CACHE]) {
    assert.ok(id >= lo && id <= hi);
    assert.match(ewp.TOOL_NAME[id], /^[a-z_]+$/);
  }
  const [slo, shi] = ewp.TOOL_ID_RANGE.system;
  assert.ok(ewp.ToolId.REBOOT >= slo && ewp.ToolId.REBOOT <= shi);
});

test('built-in watchlist is valid device input and fits one downlink frame', () => {
  const ids = new Set(ewp.DEFAULT_CATEGORIES.map((c) => c.id));
  const rules = ewp.DEFAULT_RULES.map((r) => {
    assert.ok(ids.has(r.category), r.pattern);
    const { suffix, flags } = ewp.patternToSuffix(r.pattern);
    assert.match(suffix, /^[a-z0-9-]+(\.[a-z0-9-]+)+$/, r.pattern);
    assert.ok(ewp.utf8ByteLength(suffix) <= ewp.EWP_LIMITS.watchSuffixBytes);
    assert.equal((r.flags | flags) & ~ewp.RULE_FLAGS_KNOWN_MASK, 0);
    return [suffix, r.category, r.flags | flags];
  });
  const K = ewp.SetWatchlistArgsKey;
  const body = encode(new Map<number, unknown>([
    [K.op, ewp.WatchlistOp.REPLACE],
    [K.version, ewp.DEFAULT_WATCHLIST_VERSION],
    [K.rules, rules],
    [K.categories, ewp.DEFAULT_CATEGORIES.map((c) => [c.id, c.name])],
  ]));
  assert.ok(ewp.EWP_HEADER_LEN + body.byteLength <= ewp.EWP_LIMITS.downlinkFrame);
  // YouTube is there as the user asked: youtube.com (+ subdomains), *.googlevideo.com, *.ytimg.com, youtu.be, youtubei.googleapis.com
  const yt = ewp.DEFAULT_RULES.filter((r) => r.category === 1).map((r) => r.pattern);
  for (const p of ['youtube.com', '*.googlevideo.com', '*.ytimg.com', 'youtu.be', 'youtubei.googleapis.com']) assert.ok(yt.includes(p), p);
});

test('a worst-case DNS batch entry round-trips through CBOR', () => {
  const B = ewp.DnsBatchKey;
  const q: unknown[] = [12_345, 0, 'r4---sn-xuj5gv-8vbs.googlevideo.com', 1, 0, 23, 3, ewp.DnsQueryFlag.FLAGGED, 1];
  const frame = new Map<number, unknown>([
    [B.seq, 7],
    [B.windowMs, 10_000],
    [B.clients, [[new Uint8Array([192, 168, 0, 42]), new Uint8Array([0x3c, 0x22, 0xfb, 1, 2, 3]), 'kitchen-tv']]],
    [B.queries, [q]],
  ]);
  const back = decode(encode(frame)) as Map<number, unknown>;
  assert.equal(back.get(B.seq), 7);
  assert.deepEqual((back.get(B.queries) as unknown[][])[0], q);
});
