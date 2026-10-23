/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/interop/interop.test.mjs
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Both ends of both contracts are production code here:
 *   ionity-prov/2  @ionity/prov ProvClient  <-pipe->  C++ ProvHandler (prov_host)
 *   EWP/1          @ionity/ewp key tables + cbor-x  <->  C++ encoders / MCPServer (ewp_dump)
 *
 *   FW_HOST_BUILD=/path/to/build/host node --test firmware/esp32-node/test/interop/
 * (run from the repository root; firmware/esp32-node/test/run.sh does all of it)
 */
import { spawn, execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';
import { test } from 'node:test';
import path from 'node:path';
import { Decoder, Encoder } from 'cbor-x';
import { ProvClient, ProvError, crc32Hex } from '@ionity/prov';
import {
  AckBodyKey, ChipInfoKey, EWP_HEADER_LEN, EWP_MAGIC, EWP_VERSION, ErrorBodyKey, ExecTimingKey, FirmwareInfoKey,
  FrameFlag, HEADER_LAYOUT, HeapStatsKey, HelloBodyKey, MsgType, NetStatsKey, ResultBodyKey, Status,
  StatusBodyKey, TelemetryBodyKey, TelemetrySection, ToolId,
  EventBodyKey, EventKind, DnsBatchKey, DnsFlagKey, DnsCountersKey, NetObserveInfoKey, ActuatorDescriptorKey,
  SetWatchlistArgsKey, WatchlistResultKey, GetWatchlistResultKey, NetworkStatsResultKey, LocalDevicesResultKey,
  SetActuatorArgsKey, SetActuatorResultKey, WatchlistOp, RuleFlag, DnsQueryFlag, DnsMode, NetCap,
  DEFAULT_WATCHLIST_VERSION, DEFAULT_RULES,
} from '@ionity/ewp';

const BUILD = process.env.FW_HOST_BUILD ?? path.resolve('build/host');
const bin = (name) => path.join(BUILD, name);
const decoder = new Decoder({ mapsAsObjects: false, useRecords: false });
const encoder = new Encoder({ mapsAsObjects: false, useRecords: false, variableMapSize: true });

/* ---------------------------------------------------------------- prov/2 */

/** A LineTransport over the stdio of prov_host (what SerialSession is in the portal). */
function provHost(args = []) {
  const child = spawn(bin('prov_host'), args, { stdio: ['pipe', 'pipe', 'inherit'] });
  const listeners = new Set();
  const lines = [];
  let buf = '';
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', (chunk) => {
    buf += chunk;
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).replace(/\r$/, '');
      buf = buf.slice(nl + 1);
      lines.push(line);
      for (const l of listeners) l(line);
    }
  });
  const transport = {
    onLine(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    write(text) {
      return new Promise((resolve, reject) => child.stdin.write(text, (e) => (e ? reject(e) : resolve())));
    },
  };
  return { child, transport, lines, close: () => child.stdin.end() };
}

const replies = (lines) => lines.filter((l) => l.startsWith('IONITY-PROV '));

test('prov/2: the TypeScript client provisions the C++ board end to end', async () => {
  const h = provHost();
  const client = new ProvClient(h.transport);
  try {
    const board = await client.handshake(5000);
    assert.equal(board.prov, 2);
    assert.equal(board.hwid, 'esp32-98a316e5d18c');
    assert.equal(board.lineMax, 4096);
    assert.ok(board.setFields.has('host_url') && board.setFields.has('ca_pem'));
    assert.equal(board.provisioned, false);

    const r = await client.set({
      ssid: 'IONITY-LAB-IOT', pass: 'lab-pass-123', host_url: 'mqtt://192.168.0.3:1883',
      device_uuid: '0192f1c4-7d2a-7b3e-9a10-4c5d6e7f8a9b', role: 'node', site: 'office', label: 'Riser GF',
      mcp_token: 'tok_0123456789abcdef',
    });
    assert.deepEqual(r.changed.sort(), ['device_uuid', 'host_url', 'label', 'mcp_token', 'pass', 'role', 'site', 'ssid']);
    assert.equal(r.board.hostUrl, 'mqtt://192.168.0.3:1883');
    assert.equal(r.board.deviceUuid, '0192f1c4-7d2a-7b3e-9a10-4c5d6e7f8a9b');
    assert.ok(r.board.hasPass && r.board.hasMcpToken);

    // validate-then-write: the board names the field, the portal marks it
    await assert.rejects(client.set({ ssid: 'X', site: 'Bad Site' }), (e) => e instanceof ProvError && e.field === 'site');
    await assert.rejects(client.set({ host_url: 'wss://gw.ionity.lan/ewp' }), (e) => e instanceof ProvError && e.field === 'host_url');
    assert.equal((await client.get()).ssid, 'IONITY-LAB-IOT');  // nothing of the bad set was written

    const t = await client.test(20000);
    assert.equal(t.ok, true);
    assert.equal(t.wifi.ok, true);
    assert.equal(t.wifi.ip, '192.168.0.42');
    assert.equal(t.broker.ok, true);
    assert.equal(t.board?.wifi, 'connected');

    const nets = await client.scan();
    assert.equal(nets[0].ssid, 'Afrihost Fibre DTM');  // strongest first (client-side sort)
    assert.ok(nets.find((n) => n.ssid === 'Guest')?.open);

    await client.reboot();
    const again = await client.handshake(5000);
    assert.equal(again.ssid, 'IONITY-LAB-IOT');

    // secrets never cross the wire from the board
    for (const l of h.lines) {
      assert.ok(!l.includes('lab-pass-123') && !l.includes('tok_0123456789abcdef'), `secret leaked: ${l}`);
    }
    // every reply carries a CRC that the TypeScript implementation agrees with
    for (const l of replies(h.lines)) {
      const m = / \*([0-9a-f]{8})$/.exec(l);
      assert.ok(m, `reply without CRC: ${l}`);
      assert.equal(crc32Hex(l.slice('IONITY-PROV '.length, m.index)), m[1]);
    }
  } finally {
    client.dispose();
    h.close();
  }
});

test('prov/2: corrupted and overlong lines are answered, not executed', async () => {
  const h = provHost();
  try {
    await new Promise((r) => setTimeout(r, 200));
    const json = '{"ionity":"prov","op":"set","v":2,"id":5,"ssid":"Hijack"}';
    await h.transport.write(`${json} *${crc32Hex(json).replace(/^./, (c) => (c === '0' ? '1' : '0'))}\n`);
    await h.transport.write(`{"ionity":"prov","op":"set","label":"${'x'.repeat(5000)}"}\n`);
    await h.transport.write('{"ionity":"prov","op":"get","v":2,"id":6} *' + crc32Hex('{"ionity":"prov","op":"get","v":2,"id":6}') + '\n');
    await new Promise((r) => setTimeout(r, 300));
    const rs = replies(h.lines).map((l) => JSON.parse(l.slice(12).replace(/ \*[0-9a-f]{8}$/, '')));
    assert.ok(rs.some((r) => r.op === 'error' && r.error === 'crc mismatch'));
    assert.ok(rs.some((r) => r.op === 'error' && r.error === 'line too long'));
    const get = rs.find((r) => r.id === 6);
    assert.equal(get.ssid, '');  // the corrupted set never ran
  } finally {
    h.close();
  }
});

test('prov/2: a fw 2.x board is migrated (server + mqtt_port -> host_url)', async () => {
  const h = provHost(['--fw2-nvs', '--broker-down']);
  const client = new ProvClient(h.transport);
  try {
    const board = await client.handshake(5000);
    assert.equal(board.hostUrl, 'mqtt://192.168.0.3:1883');
    assert.equal(board.ssid, 'IONITY-LAB-IOT');
    const t = await client.test(20000);
    assert.equal(t.wifi.ok, true);
    assert.equal(t.broker.ok, false);
    assert.equal(t.ok, false);
  } finally {
    client.dispose();
    h.close();
  }
});

/* ---------------------------------------------------------------- EWP/1 */

function header(bytes) {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const h = {};
  for (const f of HEADER_LAYOUT) {
    h[f.field] = f.size === 1 ? v.getUint8(f.offset) : f.size === 2 ? v.getUint16(f.offset, true) : v.getUint32(f.offset, true);
  }
  return h;
}

function decodeFrame(hex) {
  const bytes = Buffer.from(hex, 'hex');
  const h = header(bytes);
  assert.equal(h.magic, EWP_MAGIC);
  assert.equal(h.version, EWP_VERSION);
  assert.equal(h.reserved, 0);
  assert.equal(EWP_HEADER_LEN + h.bodyLen, bytes.length);
  return { h, body: decoder.decode(bytes.subarray(EWP_HEADER_LEN)) };
}

/** Every key of a decoded Map must be in the TypeScript key table. */
function keysIn(map, table, what) {
  assert.ok(map instanceof Map, `${what} is not a CBOR map`);
  const allowed = new Set(Object.values(table));
  for (const k of map.keys()) assert.ok(allowed.has(k), `${what}: unknown key ${k}`);
}

function encodeRequest({ rid, tool, flags = 0, deadlineMs = 0, args }) {
  const body = encoder.encode(args);
  const out = Buffer.alloc(EWP_HEADER_LEN + body.length);
  const v = new DataView(out.buffer, out.byteOffset, out.byteLength);
  const fields = { magic: EWP_MAGIC, version: EWP_VERSION, type: MsgType.REQ, flags, rid, tool, status: Status.OK, deadlineMs, bodyLen: body.length, reserved: 0 };
  for (const f of HEADER_LAYOUT) {
    const x = fields[f.field];
    if (f.size === 1) v.setUint8(f.offset, x);
    else if (f.size === 2) v.setUint16(f.offset, x, true);
    else v.setUint32(f.offset, x, true);
  }
  body.copy?.(out, EWP_HEADER_LEN) ?? out.set(body, EWP_HEADER_LEN);
  return out.toString('hex');
}

test('EWP/1: C++ HELLO / STATUS / TELEMETRY decode with cbor-x and the TS key tables', () => {
  const frames = Object.fromEntries(
    execFileSync(bin('ewp_dump'), ['frames'], { encoding: 'utf8' }).trim().split('\n').map((l) => l.split(' ')),
  );
  const hello = decodeFrame(frames.hello);
  assert.equal(hello.h.type, MsgType.HELLO);
  keysIn(hello.body, HelloBodyKey, 'HELLO');
  assert.deepEqual(hello.body.get(HelloBodyKey.proto), [1, 1]);
  assert.equal(hello.body.get(HelloBodyKey.hwid), 'esp32-98a316e5d18c');
  assert.equal(hello.body.get(HelloBodyKey.uuid).length, 16);
  keysIn(hello.body.get(HelloBodyKey.fw), FirmwareInfoKey, 'fw');
  keysIn(hello.body.get(HelloBodyKey.chip), ChipInfoKey, 'chip');
  const toolIds = hello.body.get(HelloBodyKey.tools).map((t) => t[0]).sort();
  assert.deepEqual(toolIds, [ToolId.GET_DEVICE_INFO, ToolId.READ_TELEMETRY, ToolId.IDENTIFY].sort());

  const st = decodeFrame(frames.status);
  keysIn(st.body, StatusBodyKey, 'STATUS');
  assert.deepEqual([...st.body.get(StatusBodyKey.ip)], [192, 168, 0, 42]);
  assert.equal(decodeFrame(frames.will).body.get(StatusBodyKey.state), 0);

  const tm = decodeFrame(frames.telemetry);
  assert.equal(tm.h.type, MsgType.TELEMETRY);
  keysIn(tm.body, TelemetryBodyKey, 'TELEMETRY');
  keysIn(tm.body.get(TelemetryBodyKey.heap), HeapStatsKey, 'heap');
  keysIn(tm.body.get(TelemetryBodyKey.net), NetStatsKey, 'net');
  assert.equal(tm.body.get(TelemetryBodyKey.seq), tm.h.rid);
  assert.ok(Math.abs(tm.body.get(TelemetryBodyKey.sensors).get(1) - 41.5) < 1e-6);
});

test('EWP/1: a TS-encoded read_telemetry REQ runs through the C++ MCPServer', () => {
  const req = encodeRequest({
    rid: 0x0100002a, tool: ToolId.READ_TELEMETRY, flags: FrameFlag.ACK_REQUESTED, deadlineMs: 3000,
    args: new Map([[1, TelemetrySection.HEAP | TelemetrySection.TASKS]]),
  });
  const bad = encodeRequest({ rid: 0x0100002b, tool: ToolId.READ_TELEMETRY, args: new Map([[1, 300]]) });
  const out = execFileSync(bin('ewp_dump'), ['req', req, bad], { encoding: 'utf8' }).trim().split('\n').map(decodeFrame);

  const ack = out.find((f) => f.h.type === MsgType.ACK);
  assert.equal(ack.h.rid, 0x0100002a);
  keysIn(ack.body, AckBodyKey, 'ACK');

  const res = out.find((f) => f.h.type === MsgType.RES);
  assert.equal(res.h.rid, 0x0100002a);
  keysIn(res.body, ResultBodyKey, 'RES');
  keysIn(res.body.get(ResultBodyKey.timing), ExecTimingKey, 'timing');
  const result = res.body.get(ResultBodyKey.result);
  keysIn(result, TelemetryBodyKey, 'result');
  assert.ok(result.has(TelemetryBodyKey.heap) && result.has(TelemetryBodyKey.tasks));
  assert.ok(!result.has(TelemetryBodyKey.net));

  const err = out.find((f) => f.h.type === MsgType.ERR);
  assert.equal(err.h.rid, 0x0100002b);
  assert.equal(err.h.status, Status.INVALID_ARGS);
  keysIn(err.body, ErrorBodyKey, 'ERR');
  assert.equal(err.body.get(ErrorBodyKey.argKey), 1);
});

/* ---------------------------------------------------------------- NetObserve */

test('NetObserve: C++ DNS_FLAGGED / DNS_BATCH / HELLO.net / TELEMETRY.dns decode with the TS key tables', () => {
  const lines = execFileSync(bin('ewp_dump'), ['netobs'], { encoding: 'utf8' }).trim().split('\n').map((l) => l.split(' '));
  const frames = Object.fromEntries(lines);

  const flag = decodeFrame(frames.flag);
  assert.equal(flag.h.type, MsgType.EVENT);
  keysIn(flag.body, EventBodyKey, 'EVENT');
  assert.equal(flag.body.get(EventBodyKey.kind), EventKind.DNS_FLAGGED);
  const f = flag.body.get(EventBodyKey.flag);
  keysIn(f, DnsFlagKey, 'DnsFlag');
  const [ip, mac, host] = f.get(DnsFlagKey.client);
  assert.deepEqual([...ip], [127, 0, 0, 1]);
  assert.deepEqual([...mac], [0x3c, 0x22, 0xfb, 0x12, 0x34, 0x56]);
  assert.equal(typeof host, 'string');
  assert.equal(f.get(DnsFlagKey.qname), 'www.youtube.com');
  assert.equal(f.get(DnsFlagKey.rule), 'youtube.com');
  assert.equal(f.get(DnsFlagKey.category), 1);
  assert.equal(f.get(DnsFlagKey.watchVer), DEFAULT_WATCHLIST_VERSION);

  const batch = decodeFrame(frames.batch);
  assert.equal(batch.body.get(EventBodyKey.kind), EventKind.DNS_BATCH);
  const b = batch.body.get(EventBodyKey.dns);
  keysIn(b, DnsBatchKey, 'DnsBatch');
  assert.equal(b.get(DnsBatchKey.clients).length, 1);
  const qs = b.get(DnsBatchKey.queries);
  assert.equal(qs.length, 2);
  const yt = qs.find((q) => q[2] === 'www.youtube.com');
  assert.equal(yt.length, 9);
  assert.equal(yt[1], 0);              // client index
  assert.equal(yt[3], 1);              // A
  assert.ok(yt[7] & DnsQueryFlag.FLAGGED);
  assert.equal(yt[8], 1);              // category youtube
  assert.equal(qs.find((q) => q[2] === 'example.org')[3], 28);

  const hello = decodeFrame(frames.hello);
  keysIn(hello.body, HelloBodyKey, 'HELLO');
  const net = hello.body.get(HelloBodyKey.net);
  keysIn(net, NetObserveInfoKey, 'HELLO.net');
  assert.equal(net.get(NetObserveInfoKey.mode), DnsMode.FORWARD);
  assert.ok(net.get(NetObserveInfoKey.caps) & NetCap.DNS_FORWARD);
  assert.equal(net.get(NetObserveInfoKey.watchVer), DEFAULT_WATCHLIST_VERSION);
  assert.equal(net.get(NetObserveInfoKey.watchRules), DEFAULT_RULES.length);
  const acts = hello.body.get(HelloBodyKey.actuators);
  assert.equal(acts.length, 2);
  for (const a of acts) keysIn(a, ActuatorDescriptorKey, 'actuator');
  assert.equal(acts[0].get(ActuatorDescriptorKey.name), 'pwm0');
  const toolIds = hello.body.get(HelloBodyKey.tools).map((t) => t[0]);
  for (const id of [ToolId.SET_WATCHLIST, ToolId.GET_WATCHLIST, ToolId.GET_NETWORK_STATS, ToolId.GET_LOCAL_DEVICES,
    ToolId.QUERY_DNS_LOG, ToolId.SCAN_LAN, ToolId.FLUSH_DNS_CACHE, ToolId.SET_ACTUATOR, ToolId.REBOOT]) {
    assert.ok(toolIds.includes(id), `tool ${id} in HELLO`);
  }

  const tm = decodeFrame(frames.telemetry);
  keysIn(tm.body, TelemetryBodyKey, 'TELEMETRY');
  const dns = tm.body.get(TelemetryBodyKey.dns);
  keysIn(dns, DnsCountersKey, 'TELEMETRY.dns');
  assert.equal(dns.get(DnsCountersKey.queries), 2);
  assert.equal(dns.get(DnsCountersKey.flagged), 1);
});

test('NetObserve: TS-encoded set_watchlist / get_watchlist / set_actuator run through the C++ node', () => {
  const W = SetWatchlistArgsKey;
  const set = encodeRequest({
    rid: 0x01000100, tool: ToolId.SET_WATCHLIST, deadlineMs: 3000,
    args: new Map([
      [W.op, WatchlistOp.REPLACE], [W.version, 1761213600],
      [W.rules, [['youtube.com', 1, RuleFlag.ALERT], ['googlevideo.com', 1, RuleFlag.ALERT | RuleFlag.SUBDOMAINS_ONLY], ['netflix.com', 3, 0]]],
      [W.categories, [[1, 'video_streaming:youtube'], [3, 'video_streaming:netflix']]],
    ]),
  });
  const get = encodeRequest({ rid: 0x01000101, tool: ToolId.GET_WATCHLIST, args: new Map() });
  const stats = encodeRequest({ rid: 0x01000102, tool: ToolId.GET_NETWORK_STATS, args: new Map() });
  const devs = encodeRequest({ rid: 0x01000103, tool: ToolId.GET_LOCAL_DEVICES, args: new Map([[1, 10]]) });
  const led = encodeRequest({
    rid: 0x01000104, tool: ToolId.SET_ACTUATOR,
    args: new Map([[SetActuatorArgsKey.channel, 0], [SetActuatorArgsKey.value, 1], [SetActuatorArgsKey.leaseMs, 20000]]),
  });
  const out = execFileSync(bin('ewp_dump'), ['netobs', set, get, stats, devs, led], { encoding: 'utf8' })
    .trim().split('\n').map((l) => l.split(' ')).filter(([n]) => n === 'reply').map(([, hex]) => decodeFrame(hex));
  const byRid = new Map(out.map((f) => [f.h.rid, f]));

  const s = byRid.get(0x01000100);
  assert.equal(s.h.type, MsgType.RES);
  const sr = s.body.get(ResultBodyKey.result);
  keysIn(sr, WatchlistResultKey, 'set_watchlist');
  assert.equal(sr.get(WatchlistResultKey.version), 1761213600);
  assert.equal(sr.get(WatchlistResultKey.rules), 3);

  const g = byRid.get(0x01000101).body.get(ResultBodyKey.result);
  keysIn(g, GetWatchlistResultKey, 'get_watchlist');
  assert.deepEqual(g.get(GetWatchlistResultKey.rules)[1], ['googlevideo.com', 1, RuleFlag.ALERT | RuleFlag.SUBDOMAINS_ONLY]);
  assert.deepEqual(g.get(GetWatchlistResultKey.categories)[1], [3, 'video_streaming:netflix']);

  const st = byRid.get(0x01000102).body.get(ResultBodyKey.result);
  keysIn(st, NetworkStatsResultKey, 'get_network_stats');
  keysIn(st.get(NetworkStatsResultKey.dns), DnsCountersKey, 'stats.dns');

  const d = byRid.get(0x01000103).body.get(ResultBodyKey.result);
  keysIn(d, LocalDevicesResultKey, 'get_local_devices');
  assert.equal(d.get(LocalDevicesResultKey.total), 1);
  assert.equal(d.get(LocalDevicesResultKey.devices)[0].length, 6);

  const a = byRid.get(0x01000104);
  assert.equal(a.h.type, MsgType.RES);
  const ar = a.body.get(ResultBodyKey.result);
  keysIn(ar, SetActuatorResultKey, 'set_actuator');
  assert.equal(ar.get(SetActuatorResultKey.applied), 1);
  assert.equal(ar.get(SetActuatorResultKey.leaseRemainingMs), 20000);
});
