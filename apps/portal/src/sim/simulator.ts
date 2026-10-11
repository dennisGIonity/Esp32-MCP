/*
 * AEDI - IONITY GLOBAL | @ionity/portal | simulator.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * ?simulate=v1 | v2 | v2-usb  - a fake Web Serial port with a simulated board
 * behind it (FakeBoard from @ionity/prov/testing):
 *
 *   v1      fw 2.1.1, ionity-prov/1, ESP32-S3 behind a CH340 USB-UART bridge
 *   v2      fw 3 (Phase 3 contract), ionity-prov/2, ESP32-C6 behind a CH340
 *   v2-usb  fw 3, ionity-prov/2, ESP32-S3 on native USB: every restart drops the
 *           port off the bus and re-enumerates it (the hard case for a portal)
 *
 * For demos, training and the Playwright run. Loaded with import() only when
 * the query parameter is present, so it is not in the normal bundle. The page
 * shows a permanent "SIMULATED BOARD" banner while it is active.
 */
import { FakeBoard, type FakeBoardOptions } from '@ionity/prov/testing';

export type SimMode = 'v1' | 'v2' | 'v2-usb';

export function parseSimMode(search: string): SimMode | null {
  const v = new URLSearchParams(search).get('simulate');
  return v === 'v1' || v === 'v2' || v === 'v2-usb' ? v : null;
}

const PROFILES: Record<SimMode, { board: FakeBoardOptions; vid: number; pid: number; label: string }> = {
  v1: {
    board: { prov: 1, chip: 'ESP32-S3', fw: '2.1.1', pace: 15 },
    vid: 0x1a86,
    pid: 0x7523,
    label: 'ESP32-S3 · firmware 2.1.1 (provisioning v1) · CH340 USB-UART',
  },
  v2: {
    board: { prov: 2, chip: 'ESP32-C6', fw: '3.0.0-sim', hwid: 'esp32-40ca63b2f1e4', pace: 15 },
    vid: 0x1a86,
    pid: 0x55d3,
    label: 'ESP32-C6 · firmware 3 (provisioning v2) · CH343 USB-UART',
  },
  'v2-usb': {
    board: { prov: 2, chip: 'ESP32-S3', fw: '3.0.0-sim', nativeUsb: true, pace: 15 },
    vid: 0x303a,
    pid: 0x1001,
    label: 'ESP32-S3 · firmware 3 (provisioning v2) · native USB (re-enumerates on reset)',
  },
};

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const lost = () => new DOMException('The device has been lost.', 'NetworkError');

class FakePort extends EventTarget {
  onconnect: ((ev: Event) => void) | null = null;
  ondisconnect: ((ev: Event) => void) | null = null;
  connected = true;
  readable: ReadableStream<Uint8Array> | null = null;
  writable: WritableStream<Uint8Array> | null = null;
  readonly board: FakeBoard;
  readonly #info: SerialPortInfo;
  #controller: ReadableStreamDefaultController<Uint8Array> | null = null;
  #rts = false;

  constructor(mode: SimMode) {
    super();
    const p = PROFILES[mode];
    this.#info = { usbVendorId: p.vid, usbProductId: p.pid };
    this.board = new FakeBoard(p.board, {
      emit: (text) => this.#emit(text),
      detach: () => this.#detach(),
      attach: () => this.#attach(),
    });
    this.board.boot(); // already running when the page connects; this boot log is not seen
  }

  getInfo(): SerialPortInfo {
    return this.#info;
  }

  async open(_options: SerialOptions): Promise<void> {
    if (!this.connected) throw lost();
    if (this.readable) throw new DOMException('The port is already open.', 'InvalidStateError');
    this.readable = new ReadableStream<Uint8Array>({
      start: (c) => {
        this.#controller = c;
      },
      cancel: () => {
        this.#controller = null;
      },
    });
    this.writable = new WritableStream<Uint8Array>({
      write: (chunk) => {
        const text = decoder.decode(chunk, { stream: true });
        // the board answers a few milliseconds later, like a real UART round trip
        setTimeout(() => this.board.receive(text), 3);
      },
    });
  }

  async close(): Promise<void> {
    this.#controller = null;
    this.readable = null;
    this.writable = null;
  }

  async setSignals(signals: SerialOutputSignals): Promise<void> {
    if (!this.readable) throw new DOMException('The port is closed.', 'InvalidStateError');
    if (signals.requestToSend === true) this.#rts = true;
    else if (signals.requestToSend === false && this.#rts) {
      this.#rts = false;
      this.board.reset(); // EN released after being held low
    }
  }

  async getSignals(): Promise<SerialInputSignals> {
    return { dataCarrierDetect: false, clearToSend: true, ringIndicator: false, dataSetReady: true };
  }

  async forget(): Promise<void> {
    await this.close();
  }

  /** USB delivers arbitrary chunk sizes: split the text so line reassembly is exercised. */
  #emit(text: string): void {
    const c = this.#controller;
    if (!c) return; // port closed: the output is lost, as on real hardware
    const bytes = encoder.encode(text);
    for (let off = 0; off < bytes.byteLength; ) {
      const n = 1 + Math.floor(Math.random() * 48);
      c.enqueue(bytes.slice(off, off + n));
      off += n;
    }
  }

  #detach(): void {
    this.connected = false;
    this.#controller?.error(lost());
    this.#controller = null;
    this.readable = null;
    this.writable = null;
    const ev = new Event('disconnect');
    this.ondisconnect?.(ev);
    this.dispatchEvent(ev);
  }

  #attach(): void {
    this.connected = true;
    const ev = new Event('connect');
    this.onconnect?.(ev);
    this.dispatchEvent(ev);
  }
}

class FakeSerial extends EventTarget {
  onconnect: ((ev: Event) => void) | null = null;
  ondisconnect: ((ev: Event) => void) | null = null;
  readonly #mode: SimMode;
  #port: FakePort | null = null;
  #granted = false;

  constructor(mode: SimMode) {
    super();
    this.#mode = mode;
  }

  get port(): FakePort {
    this.#port ??= new FakePort(this.#mode);
    return this.#port;
  }

  async requestPort(_options?: SerialPortRequestOptions): Promise<SerialPort> {
    this.#granted = true;
    return this.port as unknown as SerialPort;
  }

  async getPorts(): Promise<SerialPort[]> {
    return this.#granted ? [this.port as unknown as SerialPort] : [];
  }
}

export interface Simulation {
  readonly mode: SimMode;
  readonly label: string;
  readonly serial: Serial;
  readonly board: () => FakeBoard;
}

export function createSimulation(mode: SimMode): Simulation {
  const serial = new FakeSerial(mode);
  const sim: Simulation = {
    mode,
    label: PROFILES[mode].label,
    serial: serial as unknown as Serial,
    board: () => serial.port.board,
  };
  // Test hook for the end-to-end run (simulator builds only): read NVS, count requests.
  (globalThis as { __IONITY_SIM__?: Simulation }).__IONITY_SIM__ = sim;
  return sim;
}
