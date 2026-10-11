/*
 * AEDI - IONITY GLOBAL | @ionity/prov | client against simulated fw 2.x and fw 3 boards | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { ProvClient, ProvError, ProvTimeoutError, type LineTransport } from '../src/index.ts';
import { FakeBoard, type FakeBoardOptions } from '../src/testing/fake-board.ts';

/** In-memory serial line: board output is split into lines like SerialSession does. */
function wire(opts: FakeBoardOptions) {
  const listeners = new Set<(l: string) => void>();
  let buf = '';
  const log: string[] = [];
  const board = new FakeBoard(opts, {
    emit(text) {
      buf += text;
      let nl: number;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).replace(/\r$/, '');
        buf = buf.slice(nl + 1);
        for (const l of listeners) l(line);
      }
    },
  });
  const transport: LineTransport = {
    onLine(l) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    async write(text) {
      queueMicrotask(() => board.receive(text));
    },
  };
  const client = new ProvClient(transport, (t, k) => log.push(`${k}:${t}`));
  return { board, client, log };
}

test('fw 2.x (prov/1): boot announce, set, test, reboot', async () => {
  const { board, client, log } = wire({ prov: 1 });
  board.boot();
  const hello = await client.handshake(2000);
  assert.equal(hello.prov, 1);
  assert.equal(hello.provisioned, false);

  const r = await client.set({ ssid: 'Afrihost Fibre DTM', pass: 'correct-horse', server: '192.168.0.3', mqtt_port: 1883, role: 'node', site: 'office' });
  assert.deepEqual(r.changed.sort(), ['mqtt_port', 'pass', 'role', 'server', 'site', 'ssid']);
  assert.equal(board.received.at(-1)?.['v'], undefined, 'no prov/2 envelope sent to fw 2.x');
  assert.ok(log.every((l) => !l.includes('correct-horse')), 'password never logged');

  const t = await client.test(1000);
  assert.ok(t.ok && t.wifi.ok && t.broker.ok === true);
  await client.reboot();
  const again = await client.handshake(2000);
  assert.equal(again.wifi, 'connected');
  assert.equal(board.rebootCount, 1);
});

test('fw 3 (prov/2): ids, CRC both ways, structured test, field errors', async () => {
  const { board, client } = wire({ prov: 2 });
  board.boot();
  const hello = await client.handshake(2000);
  assert.equal(hello.prov, 2);
  await client.set({ ssid: 'IONITY-LAB-IOT', pass: 'lab-pass-123', host_url: 'mqtt://192.168.0.3', device_uuid: '0192f1c4-7d2a-7000-8000-000000000000' });
  const last = board.received.at(-1);
  assert.equal(last?.['v'], 2);
  assert.equal(typeof last?.['id'], 'number');
  assert.equal(board.nvs.get('dev_uuid'), '0192f1c4-7d2a-7000-8000-000000000000');

  const t = await client.test(1000);
  assert.ok(t.ok && t.wifi.ip === '192.168.0.42' && t.broker.ok === true);

  await assert.rejects(client.set({ ssid: '' }), (e: unknown) => e instanceof ProvError && e.field === 'ssid');
  // fw 3 refuses an unknown field and writes nothing (validate-then-write)
  const before = new Map(board.nvs);
  await assert.rejects(client.request('set', { label: 'x', bogus: 1 }), (e: unknown) => e instanceof ProvError && e.field === 'bogus');
  assert.deepEqual(board.nvs, before);
});

test('a garbled reply is detected by its CRC and the request is retried', async () => {
  const { board, client, log } = wire({ prov: 2 });
  board.boot();
  await client.handshake(2000);
  board.corruptNextReply = true;
  const b = await client.request('get', {}, { timeoutMs: 200, retries: 1 });
  assert.equal(b['op'], 'get');
  assert.ok(log.some((l) => l.includes('CRC')));
});

test('wrong Wi-Fi password is a result, not an exception', async () => {
  const { board, client } = wire({ prov: 1 });
  board.boot();
  await client.handshake(2000);
  await client.set({ ssid: 'Afrihost Fibre DTM', pass: 'not-the-password' });
  const t = await client.test(500);
  assert.equal(t.ok, false);
  assert.equal(t.wifi.error, 'wrong password or rejected');
});

test('after a downgrade to fw 2.x the hello still gets through (prov/1 envelope)', async () => {
  // One transport, the board behind it swapped: fw 3 first, then fw 2.x.
  const listeners = new Set<(l: string) => void>();
  let buf = '';
  let mute = false;
  const io = {
    emit(text: string) {
      if (mute) return; // output while the host is not listening (re-flash, reboot)
      buf += text;
      let nl: number;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).replace(/\r$/, '');
        buf = buf.slice(nl + 1);
        for (const l of listeners) l(line);
      }
    },
  };
  let board = new FakeBoard({ prov: 2 }, io);
  const transport: LineTransport = {
    onLine(l) {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    async write(text) {
      queueMicrotask(() => board.receive(text));
    },
  };
  const client = new ProvClient(transport);
  board.boot();
  assert.equal((await client.handshake(2000)).prov, 2);
  assert.equal(board.received.at(-1)?.['v'], undefined, 'hello itself is sent without the prov/2 envelope');
  await client.get(); // prov/2 request with CRC
  assert.equal(board.received.at(-1)?.['v'], 2);

  // Flashed back to fw 2.x; its boot announcement is lost, so the client still
  // believes it talks to prov/2. A CRC-suffixed hello would be ignored by fw 2.x.
  mute = true;
  board = new FakeBoard({ prov: 1 }, io);
  board.boot();
  mute = false;
  assert.equal(client.version, 2);
  const again = await client.handshake(2000);
  assert.equal(again.prov, 1);
});

test('silence times out with a clear error', async () => {
  const { board, client } = wire({ prov: 1 });
  board.boot();
  await client.handshake(2000);
  board.dropNextReply = true;
  await assert.rejects(client.request('scan', {}, { timeoutMs: 100, retries: 0 }), ProvTimeoutError);
});
