/*
 * AEDI - IONITY GLOBAL | @ionity/portal | SerialSession against simulated ports | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The real SerialSession + the real ProvClient, over the simulator's fake Web
 * Serial port with a FakeBoard behind it - the same stack the browser runs,
 * minus the browser.
 */
import { ProvClient } from '@ionity/prov';
import { afterEach, describe, expect, it } from 'vitest';
import { SerialSession } from '../src/lib/serial/session.ts';
import { createSimulation, type SimMode } from '../src/sim/simulator.ts';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
async function waitFor(cond: () => boolean, ms = 3000): Promise<void> {
  const until = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > until) throw new Error('condition not met in time');
    await sleep(10);
  }
}

const open: SerialSession[] = [];
function rig(mode: SimMode) {
  const sim = createSimulation(mode);
  const session = new SerialSession({ serial: sim.serial, reattachMs: 3000 });
  session.attach();
  open.push(session);
  const lines: string[] = [];
  session.onLine((l) => lines.push(l));
  const client = new ProvClient(session);
  return { sim, session, lines, client };
}

afterEach(async () => {
  for (const s of open.splice(0)) {
    s.detachAll();
    await s.close().catch(() => undefined);
  }
});

describe('SerialSession', () => {
  it('opens without resetting the board and identifies a fw 2.x board', async () => {
    const { session, client, sim } = rig('v1');
    await sleep(120); // the board finished booting before the page connected
    await session.choosePort();
    expect(session.getSnapshot()).toMatchObject({ status: 'open', port: { bridge: 'WCH CH340/CH343', nativeUsb: false } });
    const b = await client.handshake(2000);
    expect(b.prov).toBe(1);
    expect(sim.board().rebootCount).toBe(0); // opening the port did not pulse EN
  });

  it('provisions a fw 3 board end to end over the session (set, test)', async () => {
    const { session, client, sim } = rig('v2');
    await sleep(120);
    await session.choosePort();
    await client.handshake(2000);
    const r = await client.set({ ssid: 'IONITY-LAB-IOT', pass: 'lab-pass-123', host_url: 'mqtt://192.168.0.3:1883' });
    expect(r.changed).toContain('host_url');
    expect(sim.board().nvs.get('wifi_pass')).toBe('lab-pass-123');
    const t = await client.test(1000);
    expect(t.ok).toBe(true);
    expect(t.broker.ok).toBe(true);
  });

  it('follows a native-USB board through reboot and re-enumeration', async () => {
    const { session, client, sim, lines } = rig('v2-usb');
    await sleep(120);
    await session.choosePort();
    await client.handshake(2000);
    const states: string[] = [];
    session.subscribe(() => states.push(session.getSnapshot().status));
    await client.reboot();
    await waitFor(() => session.getSnapshot().reconnects === 1 && session.getSnapshot().status === 'open');
    expect(states).toContain('detached');
    const again = await client.handshake(3000);
    expect(again.prov).toBe(2);
    expect(sim.board().rebootCount).toBe(1);
    expect(lines.some((l) => l.includes('IONITY-PROV'))).toBe(true);
  });

  it('resets into the app with an EN pulse on RTS', async () => {
    const { session, sim } = rig('v1');
    await sleep(120);
    await session.choosePort();
    await session.resetToApp();
    await waitFor(() => sim.board().rebootCount === 1);
  });

  it('lends the port to a flasher and takes it back', async () => {
    const { session, client } = rig('v1');
    await sleep(120);
    await session.choosePort();
    const seen = await session.lease('test flasher', async (port) => {
      expect(session.getSnapshot().status).toBe('leased');
      expect(port.readable).toBeNull(); // closed for the borrower, who opens it itself
      await expect(session.close()).rejects.toThrow(/in use by the flasher/);
      await port.open({ baudRate: 115200 });
      return 'flashed';
    });
    expect(seen).toBe('flashed');
    await waitFor(() => session.getSnapshot().status === 'open');
    expect((await client.handshake(2000)).prov).toBe(1);
  });

  it('a throwing listener does not end the read loop', async () => {
    const { session, client } = rig('v1');
    await sleep(120);
    const errors = console.error;
    console.error = () => undefined;
    try {
      session.onBytes(() => {
        throw new Error('listener bug');
      });
      await session.choosePort();
      expect((await client.handshake(2000)).prov).toBe(1);
      expect(session.getSnapshot().status).toBe('open');
    } finally {
      console.error = errors;
    }
  });
});

describe('SerialSession read errors', () => {
  it('survives a BufferOverrunError and keeps reading', async () => {
    // A port whose first readable errors with BufferOverrunError, then a fresh one works.
    let streams = 0;
    let ctl: ReadableStreamDefaultController<Uint8Array> | null = null;
    const port = Object.assign(new EventTarget(), {
      connected: true,
      readable: null as ReadableStream<Uint8Array> | null,
      writable: new WritableStream<Uint8Array>(),
      getInfo: () => ({ usbVendorId: 0x10c4, usbProductId: 0xea60 }),
      async open() {
        port.readable = makeReadable();
      },
      async close() {
        port.readable = null;
      },
      async setSignals() {},
    });
    function makeReadable(): ReadableStream<Uint8Array> {
      streams++;
      return new ReadableStream<Uint8Array>({
        start: (c) => {
          ctl = c;
        },
      });
    }
    const serial = Object.assign(new EventTarget(), { requestPort: async () => port, getPorts: async () => [port] });
    const session = new SerialSession({ serial: serial as unknown as Serial });
    const notices: string[] = [];
    const got: string[] = [];
    session.onNotice((n) => notices.push(n.text));
    session.onLine((l) => got.push(l));
    await session.choosePort();
    // Chrome replaces port.readable after a recoverable error.
    const old = ctl as ReadableStreamDefaultController<Uint8Array> | null;
    port.readable = makeReadable();
    old?.error(new DOMException('overrun', 'BufferOverrunError'));
    await waitFor(() => streams === 2 && notices.length === 1);
    (ctl as ReadableStreamDefaultController<Uint8Array> | null)?.enqueue(new TextEncoder().encode('still here\n'));
    await waitFor(() => got.includes('still here'));
    expect(session.getSnapshot().status).toBe('open');
    expect(notices[0]).toMatch(/BufferOverrunError/);
  });
});
