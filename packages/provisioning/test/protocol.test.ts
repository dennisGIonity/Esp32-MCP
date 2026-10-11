/*
 * AEDI - IONITY GLOBAL | @ionity/prov | framing, URL, UUID, schema, compat tests | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  EMPTY_FORM,
  NVS_KEY,
  PROV_V1_LINE_MAX,
  crc32,
  crc32Hex,
  formatRequest,
  normalizeBoard,
  normalizeTest,
  normalizeUuid,
  parseBoardLine,
  parseHostUrl,
  planSet,
  redact,
  toV1Server,
  utf8Encode,
  uuidv7,
  uuidv7Time,
  validateForm,
  type ProvisionForm,
  type ValidForm,
} from '../src/index.ts';

const enc = (s: string) => Uint8Array.from(Buffer.from(s, 'utf8'));

test('CRC-32 check value and UTF-8 encoder agree with Node', () => {
  assert.equal(crc32(utf8Encode('123456789')), 0xcbf43926);
  for (const s of ['', 'a', 'Ü€😀', 'IONITY-PROV {"ok":true}']) assert.deepEqual(utf8Encode(s), enc(s));
  assert.equal(crc32Hex('123456789'), 'cbf43926');
});

test('framing: prov/1 lines, prov/2 CRC suffix, corruption and log lines', () => {
  assert.equal(parseBoardLine('I (312) wifi: connected'), null);
  assert.equal(parseBoardLine('IONITY-PROV {"ok":true,"op":"hel'), null); // truncated
  const v1 = parseBoardLine('IONITY-PROV {"ok":true,"op":"hello","device_id":"esp32-98a316e5d18c"}');
  assert.equal(v1?.crc, 'absent');
  const text = '{"ok":true,"op":"get","id":7}';
  const good = parseBoardLine(`IONITY-PROV ${text} *${crc32Hex(text)}`);
  assert.equal(good?.crc, 'ok');
  assert.equal(good?.json['id'], 7);
  assert.equal(parseBoardLine(`IONITY-PROV ${text} *00000000`)?.crc, 'mismatch');
  // a log line glued in front of the prefix (no newline between tasks) still parses
  assert.equal(parseBoardLine(`W (9) x: y IONITY-PROV ${text}`)?.json['op'], 'get');

  const v1req = formatRequest({ op: 'set', ssid: 'Lab' }, { crc: false });
  assert.equal(v1req, '{"ionity":"prov","op":"set","ssid":"Lab"}\n');
  const v2req = formatRequest({ op: 'get', v: 2, id: 3 }, { crc: true });
  const m = / \*([0-9a-f]{8})\n$/.exec(v2req);
  assert.ok(m);
  assert.equal(crc32Hex(v2req.slice(0, m.index)), m[1]);
});

test('NVS keys fit ESP-IDF (15 chars) and are unique', () => {
  const keys = Object.values(NVS_KEY);
  assert.ok(keys.every((k) => k.length <= 15));
  assert.equal(new Set(keys).size, keys.length);
});

test('MCP host URL parsing', () => {
  const bare = parseHostUrl('192.168.0.3');
  assert.ok(bare.ok);
  assert.equal(bare.ok && bare.value.href, 'mqtt://192.168.0.3:1883'); // port always explicit
  assert.equal(bare.ok && bare.value.port, 1883);
  const tls = parseHostUrl('MQTTS://Fleet.Ionity.LAN');
  assert.ok(tls.ok && tls.value.href === 'mqtts://fleet.ionity.lan:8883' && tls.value.port === 8883 && tls.value.tls);
  const legacy = parseHostUrl('http://192.168.0.3');
  assert.equal(legacy.ok && legacy.value.href, 'http://192.168.0.3:8099', 'http defaults to the fleet host port, written out'); 
  const ws = parseHostUrl('wss://gw.ionity.lan:9443/ewp');
  assert.ok(ws.ok && ws.value.path === '/ewp' && ws.value.href === 'wss://gw.ionity.lan:9443/ewp');
  assert.ok(parseHostUrl('mqtt://ionity-fleet.local:1884').ok);
  assert.ok(parseHostUrl('mqtt://[fd00::3]:1883').ok);
  for (const bad of ['mqtt://user:pw@host', 'ftp://host', 'mqtt://host:0', 'mqtt://host:70000', 'mqtt://host/path', 'mqtt://-bad-.lan', 'mqtt://999.1.1.1', 'mqtt://host?x=1', 'mqtt://ho st']) {
    assert.equal(parseHostUrl(bad).ok, false, bad);
  }
  const must = (s: string) => {
    const r = parseHostUrl(s);
    if (!r.ok) throw new Error(r.error);
    return r.value;
  };
  assert.deepEqual(toV1Server(must('mqtt://192.168.0.3:1884')), { ok: true, server: '192.168.0.3', mqtt_port: 1884 });
  assert.equal(toV1Server(must('mqtts://h')).ok, false);
});

test('UUIDv7: layout, ordering, timestamp', () => {
  const zero = (b: Uint8Array) => b.fill(0);
  const u = uuidv7(0x0192_f1c4_7d2a, zero);
  assert.equal(u, '0192f1c4-7d2a-7000-8000-000000000000');
  assert.equal(uuidv7Time(u), 0x0192_f1c4_7d2a);
  const a = uuidv7(1_000);
  const b = uuidv7(2_000);
  assert.ok(a < b, 'time-ordered');
  assert.equal(normalizeUuid('{0192F1C4-7D2A-7000-8000-000000000000}'), u);
  assert.equal(normalizeUuid('not-a-uuid'), null);
});

const form = (over: Partial<ProvisionForm>): ProvisionForm => ({ ...EMPTY_FORM, ssid: 'Afrihost Fibre DTM', pass: 'correct-horse', hostUrl: 'mqtt://192.168.0.3', ...over });

test('form validation: errors land on the right field', () => {
  assert.equal(validateForm(form({})).ok, true);
  const cases: [Partial<ProvisionForm>, keyof ProvisionForm][] = [
    [{ ssid: '' }, 'ssid'],
    [{ ssid: 'x'.repeat(33) }, 'ssid'],
    [{ pass: 'short' }, 'pass'],
    [{ pass: '' }, 'pass'],
    [{ openNetwork: true }, 'pass'],
    [{ hostUrl: 'mqtt://user:pw@h' }, 'hostUrl'],
    [{ deviceUuid: '1234' }, 'deviceUuid'],
    [{ site: 'Kelvin Drive' }, 'site'],
    [{ mcpToken: 'short' }, 'mcpToken'],
    [{ caPem: 'hello' }, 'caPem'],
  ];
  for (const [over, field] of cases) {
    const r = validateForm(form(over));
    assert.equal(r.ok, false, JSON.stringify(over));
    assert.ok(!r.ok && r.errors[field], `${field} for ${JSON.stringify(over)}`);
  }
  // 32 bytes of multi-byte SSID is fine, 33 is not
  assert.equal(validateForm(form({ ssid: 'Ü'.repeat(16) })).ok, true);
  assert.equal(validateForm(form({ ssid: 'Ü'.repeat(16) + 'x' })).ok, false);
  // open network, keep-password, raw PSK
  assert.equal(validateForm(form({ openNetwork: true, pass: '' })).ok, true);
  assert.equal(validateForm(form({ keepPass: true, pass: '' })).ok, true);
  const psk = validateForm(form({ pass: 'a'.repeat(64) }));
  assert.ok(psk.ok && psk.value.rawPsk);
  // warnings, not errors
  const w = validateForm(form({ pass: 'Überwachung', mqttPass: 'secret-pass' }));
  assert.ok(w.ok);
  assert.equal(w.warnings.length, 2);
  assert.ok(validateForm(form({ hostUrl: '' })).warnings.some((x) => x.includes('mDNS')));
});

const v1Hello = normalizeBoard({
  ok: true, op: 'hello', device_id: 'esp32-98a316e5d18c', fw: '2.1.1', chip: 'ESP32-S3', provisioned: true,
  ssid: 'Afrihost Fibre DTM', has_pass: true, server: '192.168.0.3', mqtt_port: 1883, http_port: 8099, role: 'node',
  site: 'office', group: 'demo', label: 'Demo S3 #1 (red light)', wifi: 'connected', ip: '192.168.0.42', rssi: -44,
  mcp_url: 'http://192.168.0.42/mcp', mqtt: true,
});
const v2Hello = normalizeBoard({
  ok: true, op: 'hello', prov: 2, line_max: 4096, hwid: 'esp32-98a316e5d18c', fw: '3.0.0', host_url: 'mqtts://fleet.ionity.lan',
  device_uuid: '0192F1C4-7D2A-7000-8000-000000000000', set_fields: ['ssid', 'pass', 'host_url', 'device_uuid', 'ca_pem', 'role', 'site', 'group', 'label', 'mqtt_user', 'mqtt_pass', 'mcp_token', 'fleet_token'],
  provisioned: true, has_pass: true, wifi: 'down',
});

test('board normalisation keeps prov/1 and prov/2 apart', () => {
  assert.equal(v1Hello.prov, 1);
  assert.equal(v1Hello.hwid, 'esp32-98a316e5d18c');
  assert.equal(v1Hello.hostUrl, 'mqtt://192.168.0.3:1883');
  assert.equal(v1Hello.boardMcpUrl, 'http://192.168.0.42/mcp'); // the board's own endpoint, not the host
  assert.equal(v1Hello.lineMax, PROV_V1_LINE_MAX);
  assert.equal(v2Hello.prov, 2);
  assert.equal(v2Hello.deviceUuid, '0192f1c4-7d2a-7000-8000-000000000000');
  assert.equal(v2Hello.hostUrl, 'mqtts://fleet.ionity.lan');
  assert.equal(normalizeTest({ op: 'test', ok: false, error: 'wrong password or rejected' }).wifi.error, 'wrong password or rejected');
  const t2 = normalizeTest({ op: 'test', ok: true, wifi: { ok: true, ip: '10.0.0.9', rssi: -50 }, broker: { ok: true, detail: 'connected' } });
  assert.ok(t2.wifi.ok && t2.broker.ok === true && t2.wifi.ip === '10.0.0.9');
});

test('set plan: fw 2.x gets server/ports, fw 3 gets host_url + uuid', () => {
  const valid = (over: Partial<ProvisionForm>): ValidForm => {
    const r = validateForm(form(over));
    assert.ok(r.ok, JSON.stringify(!r.ok && r.errors));
    return r.value;
  };
  const p1 = planSet(valid({ deviceUuid: uuidv7(), site: 'office', mcpToken: 'a'.repeat(36) }), v1Hello);
  assert.equal(p1.fields.server, '192.168.0.3');
  assert.equal(p1.fields.mqtt_port, 1883);
  assert.equal(p1.fields.host_url, undefined);
  assert.equal(p1.fields.device_uuid, undefined);
  assert.ok(p1.warnings.some((w) => w.includes('UUID')));
  assert.deepEqual(p1.errors, {});
  assert.ok(planSet(valid({ hostUrl: 'mqtts://fleet.ionity.lan' }), v1Hello).errors.hostUrl);
  assert.ok(planSet(valid({ pass: 'b'.repeat(64) }), v1Hello).errors.pass);

  const uuid = uuidv7();
  const p2 = planSet(valid({ hostUrl: 'wss://gw.ionity.lan/ewp', deviceUuid: uuid, keepPass: true, pass: '' }), v2Hello);
  assert.equal(p2.fields.host_url, 'wss://gw.ionity.lan:443/ewp');
  assert.equal(p2.fields.device_uuid, uuid);
  assert.equal('pass' in p2.fields, false, 'keep-password sends no pass');
  // a label that is too long for fw 2.x's 767-byte line is caught before sending
  const tooLong = planSet(valid({ fleetToken: 'f'.repeat(128), mcpToken: 'm'.repeat(128), mqttUser: 'u'.repeat(64), mqttPass: 'p'.repeat(128), label: 'L'.repeat(48), caPem: '' }), { ...v1Hello, lineMax: 400 });
  assert.ok(tooLong.formError);
});

test('redaction hides every secret', () => {
  const r = redact({ op: 'set', ssid: 'Lab', pass: 'pw', mqtt_pass: 'x', fleet_token: 't', mcp_token: 'm', ota_pass: 'o', ca_pem: 'PEM...' });
  assert.equal(JSON.stringify(r).includes('pw'), false);
  assert.equal(r['pass'], '•••');
  assert.equal(r['ca_pem'], '<6 chars>');
  assert.equal(r['ssid'], 'Lab');
});
