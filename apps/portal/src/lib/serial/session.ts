/*
 * AEDI - IONITY GLOBAL | @ionity/portal | session.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * SerialSession - the ONLY owner of a Web Serial port in the portal.
 *
 * A SerialPort's readable stream can have exactly one reader. The terminal,
 * the provisioning client and esptool-js all need the port, so they never
 * touch it directly:
 *   - terminal       subscribes to onBytes()        (raw bytes, ANSI colours intact)
 *   - prov client    subscribes to onLine()/write() (LineTransport)
 *   - esptool-js     borrows the port via lease()   (reader stopped, port closed,
 *                    handed over, reopened afterwards)
 *
 * Edge cases handled here, so the UI does not have to:
 *   - Recoverable read errors (BufferOverrunError, BreakError, FramingError,
 *     ParityError) replace port.readable; the loop takes a new reader.
 *   - Native-USB boards (ESP32-S3/C3/C6 USB-Serial-JTAG) disappear on every
 *     chip reset and enumerate again: status goes "detached" and the session
 *     reopens the port when it reconnects - no new click needed.
 *   - Opening the port must not reset the board: DTR/RTS are released right
 *     after open (auto-reset circuits on CH340/CP210x boards).
 *   - Writes are serialised (one writer at a time) and time out instead of
 *     hanging forever on a stalled USB endpoint.
 *
 * The constructor has no side effects (React StrictMode may construct twice);
 * call attach() from an effect and detachAll() in its cleanup.
 */
import type { LineTransport } from '@ionity/prov';
import { LineSplitter } from './lines.ts';
import { describePort, sameUsbDevice, USB_FILTERS, type PortDescription } from './usb.ts';

export type SessionStatus = 'idle' | 'opening' | 'open' | 'leased' | 'detached' | 'closing' | 'error';

export interface SessionSnapshot {
  readonly status: SessionStatus;
  readonly port: PortDescription | null;
  readonly error: string | null;
  /** times the port came back after a reset / re-enumeration */
  readonly reconnects: number;
}

export interface Notice {
  readonly tone: 'info' | 'warn' | 'error';
  readonly text: string;
}

export interface SessionOptions {
  readonly baudRate?: number;
  /** read buffer; Web Serial's default (255 B) overruns on ESP-IDF boot logs */
  readonly bufferSize?: number;
  /** injectable for tests and the simulator */
  readonly serial?: Serial | null;
  readonly writeTimeoutMs?: number;
  /**
   * Pause between 256-byte write chunks. fw 2.x on a USB-UART bridge reads into a
   * 256-byte Arduino RX buffer; a long `set` line written in one burst can overrun it
   * while the sketch is busy. ~22 ms is one chunk's time on the wire at 115200 baud.
   */
  readonly interChunkDelayMs?: number;
  /** how long to wait for a native-USB board to enumerate again */
  readonly reattachMs?: number;
}

const RECOVERABLE_READ_ERRORS: ReadonlySet<string> = new Set(['BufferOverrunError', 'BreakError', 'FramingError', 'ParityError']);
const WRITE_CHUNK = 256;
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const encoder = new TextEncoder();

export class SerialSession implements LineTransport {
  readonly #serial: Serial | null;
  readonly #baudRate: number;
  readonly #bufferSize: number;
  readonly #writeTimeoutMs: number;
  readonly #interChunkDelayMs: number;
  readonly #reattachMs: number;

  #port: SerialPort | null = null;
  #snapshot: SessionSnapshot = { status: 'idle', port: null, error: null, reconnects: 0 };
  readonly #stateListeners = new Set<() => void>();
  readonly #byteListeners = new Set<(chunk: Uint8Array) => void>();
  readonly #lineListeners = new Set<(line: string) => void>();
  readonly #noticeListeners = new Set<(n: Notice) => void>();
  readonly #splitter = new LineSplitter();
  #reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  #readLoop: Promise<void> | null = null;
  #writeChain: Promise<void> = Promise.resolve();
  #stopping = false;
  #attached = false;

  constructor(opts: SessionOptions = {}) {
    this.#serial = opts.serial !== undefined ? opts.serial : typeof navigator !== 'undefined' && 'serial' in navigator ? navigator.serial : null;
    this.#baudRate = opts.baudRate ?? 115_200;
    this.#bufferSize = opts.bufferSize ?? 64 * 1024;
    this.#writeTimeoutMs = opts.writeTimeoutMs ?? 3000;
    this.#interChunkDelayMs = opts.interChunkDelayMs ?? 25;
    this.#reattachMs = opts.reattachMs ?? 15_000;
  }

  get supported(): boolean {
    return this.#serial !== null;
  }

  // ------------------------------------------------------------- store (useSyncExternalStore)
  readonly subscribe = (listener: () => void): (() => void) => {
    this.#stateListeners.add(listener);
    return () => this.#stateListeners.delete(listener);
  };

  readonly getSnapshot = (): SessionSnapshot => this.#snapshot;

  #set(patch: Partial<SessionSnapshot>): void {
    this.#snapshot = { ...this.#snapshot, ...patch };
    for (const l of this.#stateListeners) l();
  }

  // ------------------------------------------------------------- data subscriptions
  onBytes(listener: (chunk: Uint8Array) => void): () => void {
    this.#byteListeners.add(listener);
    return () => this.#byteListeners.delete(listener);
  }

  onLine(listener: (line: string) => void): () => void {
    this.#lineListeners.add(listener);
    return () => this.#lineListeners.delete(listener);
  }

  onNotice(listener: (n: Notice) => void): () => void {
    this.#noticeListeners.add(listener);
    return () => this.#noticeListeners.delete(listener);
  }

  #notice(tone: Notice['tone'], text: string): void {
    for (const l of this.#noticeListeners) l({ tone, text });
  }

  // ------------------------------------------------------------- global listeners
  /** Listen for re-enumeration of a port we already have permission for. */
  attach(): void {
    if (this.#attached || !this.#serial) return;
    this.#serial.addEventListener('connect', this.#onSerialConnect);
    this.#attached = true;
  }

  detachAll(): void {
    this.#serial?.removeEventListener('connect', this.#onSerialConnect);
    this.#attached = false;
  }

  readonly #onSerialConnect = (ev: Event): void => {
    // The event fires at the SerialPort and bubbles to navigator.serial.
    const port = ev.target;
    if (!(port instanceof EventTarget) || port === this.#serial) return;
    void this.#maybeReattach(port as SerialPort);
  };

  readonly #onPortConnect = (ev: Event): void => {
    void this.#maybeReattach(ev.target as SerialPort);
  };

  readonly #onPortDisconnect = (ev: Event): void => {
    if (ev.target !== this.#port) return;
    const st = this.#snapshot.status;
    if (st === 'leased' || st === 'detached' || st === 'idle' || st === 'closing') return; // esptool's own resets / already reported
    this.#set({ status: 'detached', error: null });
    this.#notice('warn', 'Board left the USB bus (reset or cable). Waiting for it to come back…');
  };

  async #maybeReattach(port: SerialPort): Promise<void> {
    if (this.#snapshot.status !== 'detached') return;
    const known = this.#snapshot.port;
    // Prefer the same object (Chrome reuses it for a re-enumerated device). Matching on
    // VID/PID alone could pick a different board of the same model - only when we must.
    if (port !== this.#port && !(known && sameUsbDevice(describePort(port), known))) return;
    try {
      await this.#openPort(port, true);
    } catch {
      // #openPort already put the reason into the snapshot (status "error").
    }
  }

  // ------------------------------------------------------------- open / close
  /** Must be called from a user gesture (click): shows the browser's port picker. */
  async choosePort(): Promise<void> {
    if (!this.#serial) throw new Error('Web Serial is not available in this browser. Use Chrome, Edge or Opera.');
    const port = await this.#serial.requestPort({ filters: USB_FILTERS });
    await this.open(port);
  }

  /** Reconnect to a port granted on an earlier visit, if exactly one is plugged in. */
  async resumeGranted(): Promise<boolean> {
    if (!this.#serial || this.#port) return false;
    const ports = (await this.#serial.getPorts()).filter((p) => p.connected !== false);
    const only = ports.length === 1 ? ports[0] : undefined;
    if (!only) return false;
    await this.open(only);
    return true;
  }

  async open(port: SerialPort): Promise<void> {
    if (this.#port && this.#port !== port) await this.close();
    await this.#openPort(port, false);
  }

  async #openPort(port: SerialPort, isReattach: boolean): Promise<void> {
    if (this.#port !== port) {
      this.#port?.removeEventListener('disconnect', this.#onPortDisconnect);
      this.#port?.removeEventListener('connect', this.#onPortConnect);
      this.#port = port;
      port.addEventListener('disconnect', this.#onPortDisconnect);
      port.addEventListener('connect', this.#onPortConnect);
    }
    this.#set({ status: 'opening', port: describePort(port), error: null });
    try {
      if (!port.readable) await port.open({ baudRate: this.#baudRate, bufferSize: this.#bufferSize });
      await this.#releaseResetLines(port);
    } catch (e) {
      const err = e as DOMException;
      if (isReattach && (err.name === 'NetworkError' || err.name === 'NotFoundError')) {
        this.#set({ status: 'detached' }); // still enumerating; the connect event will retry
        return;
      }
      this.#set({ status: 'error', error: openErrorText(err) });
      throw new Error(openErrorText(err));
    }
    this.#splitter.reset();
    this.#set({ status: 'open', reconnects: this.#snapshot.reconnects + (isReattach ? 1 : 0) });
    if (isReattach) this.#notice('info', 'Board is back on USB - console reattached.');
    this.#startReading(port);
  }

  async #releaseResetLines(port: SerialPort): Promise<void> {
    try {
      await port.setSignals({ dataTerminalReady: false, requestToSend: false });
    } catch {
      // Native USB CDC without line-state support: nothing to release.
    }
  }

  async close(): Promise<void> {
    const port = this.#port;
    if (!port) return;
    // Closing under esptool-js would leave the chip half-written: the UI disables
    // "Disconnect" during a lease and this guard catches anything that slips through.
    if (this.#snapshot.status === 'leased') throw new Error('the port is in use by the flasher - wait for it to finish');
    this.#set({ status: 'closing' });
    await this.#stopReading();
    await this.#writeChain.catch(() => undefined);
    try {
      await port.close();
    } catch {
      // already closed or gone
    }
    port.removeEventListener('disconnect', this.#onPortDisconnect);
    port.removeEventListener('connect', this.#onPortConnect);
    this.#port = null;
    this.#set({ status: 'idle', port: null, error: null });
  }

  /** Revoke the browser permission for this board (it disappears from getPorts()). */
  async forget(): Promise<void> {
    const port = this.#port;
    await this.close();
    await port?.forget().catch(() => undefined);
  }

  // ------------------------------------------------------------- reading
  #startReading(port: SerialPort): void {
    this.#stopping = false;
    this.#readLoop = this.#runReadLoop(port);
  }

  async #runReadLoop(port: SerialPort): Promise<void> {
    while (this.#port === port && port.readable && !this.#stopping) {
      const reader = port.readable.getReader();
      this.#reader = reader;
      let fatal = false;
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) {
            fatal = true; // cancelled by #stopReading
            break;
          }
          if (value && value.byteLength > 0) this.#ingest(value);
        }
      } catch (e) {
        const name = (e as DOMException).name;
        if (RECOVERABLE_READ_ERRORS.has(name)) {
          this.#notice('warn', `Serial ${name} - some bytes were lost, continuing.`);
        } else {
          fatal = true;
          if (!this.#stopping && this.#snapshot.status === 'open') {
            this.#set({ status: 'detached' });
            this.#notice('warn', `Serial read stopped (${name}). Waiting for the board…`);
          }
        }
      } finally {
        try {
          reader.releaseLock();
        } catch {
          // lock already released
        }
        this.#reader = null;
      }
      if (fatal) break;
    }
  }

  #ingest(chunk: Uint8Array): void {
    // A throwing listener must never end the read loop: that would look like an
    // unplugged board. Errors are reported and the bytes keep flowing.
    for (const l of this.#byteListeners) guard(() => l(chunk));
    for (const line of this.#splitter.push(chunk)) for (const l of this.#lineListeners) guard(() => l(line));
  }

  async #stopReading(): Promise<void> {
    this.#stopping = true;
    try {
      await this.#reader?.cancel();
    } catch {
      // stream already errored
    }
    try {
      await this.#readLoop;
    } catch {
      // loop errors are reported as notices
    }
    this.#readLoop = null;
  }

  // ------------------------------------------------------------- writing
  /** LineTransport: serialised, chunked, with a timeout. */
  write(data: string | Uint8Array): Promise<void> {
    const bytes = typeof data === 'string' ? encoder.encode(data) : data;
    const run = async (): Promise<void> => {
      const port = this.#port;
      if (!port?.writable || this.#snapshot.status !== 'open') throw new Error(`port is not open (${this.#snapshot.status})`);
      const writer = port.writable.getWriter();
      try {
        for (let off = 0; off < bytes.byteLength; off += WRITE_CHUNK) {
          if (off > 0 && this.#interChunkDelayMs > 0) await sleep(this.#interChunkDelayMs);
          await withTimeout(writer.write(bytes.subarray(off, off + WRITE_CHUNK)), this.#writeTimeoutMs, 'serial write timed out (is the board stuck?)');
        }
      } finally {
        writer.releaseLock();
      }
    };
    const p = this.#writeChain.then(run, run);
    this.#writeChain = p.catch(() => undefined);
    return p;
  }

  // ------------------------------------------------------------- control lines
  /**
   * Reset into the application (not the ROM bootloader): EN pulsed low via RTS
   * while IO0 stays high (DTR released). Native-USB boards re-enumerate after this.
   */
  async resetToApp(): Promise<void> {
    const port = this.#port;
    if (!port?.readable) return;
    try {
      await port.setSignals({ dataTerminalReady: false, requestToSend: true });
      await sleep(120);
      await port.setSignals({ dataTerminalReady: false, requestToSend: false });
      this.#notice('info', 'Reset sent (EN pulse).');
    } catch (e) {
      this.#notice('warn', `This port cannot drive the reset line (${(e as Error).message}). Press the board's RESET button.`);
    }
  }

  // ------------------------------------------------------------- lease (esptool-js)
  /**
   * Hand the raw port to another owner (esptool-js) and take it back afterwards.
   * The reader is stopped and the port closed first, because the borrower opens
   * it itself. After the lease the session reopens the port - immediately for
   * USB-UART bridges, after re-enumeration for native-USB boards.
   */
  async lease<T>(holder: string, fn: (port: SerialPort) => Promise<T>): Promise<T> {
    const port = this.#port;
    if (!port) throw new Error('no board selected');
    if (this.#snapshot.status === 'leased') throw new Error('the port is already in use');
    await this.#stopReading();
    await this.#writeChain.catch(() => undefined);
    try {
      if (port.readable) await port.close();
    } catch {
      // closing a vanished port
    }
    this.#set({ status: 'leased', error: null });
    this.#notice('info', `Serial port handed to ${holder}.`);
    try {
      return await fn(port);
    } finally {
      try {
        if (port.readable) await port.close();
      } catch {
        // the borrower closed it
      }
      this.#set({ status: 'detached' });
      await this.#reattachAfterLease(port);
    }
  }

  /** Current status, read through a call so TypeScript does not keep a stale narrowing across awaits. */
  #status(): SessionStatus {
    return this.#snapshot.status;
  }

  async #reattachAfterLease(port: SerialPort): Promise<void> {
    const until = Date.now() + this.#reattachMs;
    while (Date.now() < until && this.#status() === 'detached' && this.#port === port) {
      // `connected` is missing in older Chromium: then simply try to open.
      if (port.connected !== false) {
        try {
          await this.#openPort(port, true);
        } catch {
          return; // a hard error (e.g. port taken by another program) is in the snapshot
        }
        if (this.#status() === 'open') return;
      }
      await sleep(400);
    }
    if (this.#status() === 'detached') {
      this.#notice('warn', 'The board did not come back on USB. Press RESET, or reconnect it with "Connect board".');
    }
  }
}

function guard(fn: () => void): void {
  try {
    fn();
  } catch (e) {
    console.error('[serial] listener failed', e);
  }
}

function openErrorText(e: DOMException): string {
  switch (e.name) {
    case 'InvalidStateError':
      return 'The port is already open in another tab or program (close Arduino IDE / serial monitors).';
    case 'NetworkError':
      return 'The port could not be opened - is another program using it?';
    case 'NotFoundError':
      return 'The board is no longer connected.';
    case 'SecurityError':
      return 'Web Serial is blocked here (it needs https:// or localhost and no blocking policy).';
    default:
      return `${e.name}: ${e.message}`;
  }
}

function withTimeout<T>(p: Promise<T>, ms: number, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return Promise.race([
    p,
    new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}
