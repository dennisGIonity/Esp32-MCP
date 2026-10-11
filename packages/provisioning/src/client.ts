/*
 * AEDI - IONITY GLOBAL | @ionity/prov - ionity-prov/2 | client.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Request/reply client over any line transport (Web Serial in the portal,
 * a serial port in a Node CLI, an in-memory board in tests).
 *
 * Correlation: prov/2 replies echo "id"; prov/1 replies only carry "op", so
 * for fw 2.x the oldest pending request with that op wins (a "hello" reply
 * also answers a pending "get" - fw 2.x announces hello on its own at boot).
 *
 * Retries: hello/get/set/scan are idempotent (set writes the same values) and
 * retried on timeout or CRC failure. test is NOT retried (it blocks the board
 * for up to its timeout); reboot/factory_reset are never retried.
 */
import { normalizeBoard, normalizeNetworks, normalizeTest, type BoardInfo, type Network, type TestResult } from './board.ts';
import { redact, type SetFields } from './fields.ts';
import { formatRequest, parseBoardLine, type ProvVersion } from './framing.ts';

export type ProvOp = 'hello' | 'get' | 'set' | 'scan' | 'test' | 'reboot' | 'factory_reset';

/** What the client needs from a transport. SerialSession implements it. */
export interface LineTransport {
  onLine(listener: (line: string) => void): () => void;
  write(text: string): Promise<void>;
}

export type ClientLog = (text: string, kind: 'tx' | 'info' | 'warn') => void;

export class ProvError extends Error {
  readonly op: string;
  /** prov/2: the set field the board rejected */
  readonly field: string | undefined;
  readonly reply: Readonly<Record<string, unknown>> | undefined;
  constructor(op: string, message: string, field?: string, reply?: Readonly<Record<string, unknown>>) {
    super(message);
    this.name = 'ProvError';
    this.op = op;
    this.field = field;
    this.reply = reply;
  }
}

export class ProvTimeoutError extends ProvError {
  constructor(op: string, ms: number) {
    super(op, `no "${op}" reply from the board in ${Math.round(ms / 100) / 10} s`);
    this.name = 'ProvTimeoutError';
  }
}

/** Timers via globalThis: the package compiles without DOM or Node typings and runs in both. */
const timers = globalThis as unknown as {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(id: unknown): void;
};

interface Pending {
  readonly op: ProvOp;
  readonly id: number;
  readonly resolve: (json: Readonly<Record<string, unknown>>) => void;
  readonly reject: (e: Error) => void;
  readonly timer: unknown;
}

export interface RequestOptions {
  readonly timeoutMs?: number;
  readonly retries?: number;
}

const DEFAULT_TIMEOUT: Record<ProvOp, number> = {
  hello: 2500,
  get: 3000,
  set: 6000,
  scan: 15_000,
  test: 26_000,
  reboot: 3000,
  factory_reset: 4000,
};
const IDEMPOTENT: ReadonlySet<ProvOp> = new Set<ProvOp>(['hello', 'get', 'set', 'scan']);

const sleep = (ms: number) => new Promise<void>((r) => void timers.setTimeout(r, ms));

export class ProvClient {
  readonly #transport: LineTransport;
  readonly #log: ClientLog;
  readonly #unsubscribe: () => void;
  readonly #boardListeners = new Set<(b: BoardInfo) => void>();
  #pending: Pending[] = [];
  #nextId = 1;
  #board: BoardInfo | undefined;
  #disposed = false;

  constructor(transport: LineTransport, log: ClientLog = () => undefined) {
    this.#transport = transport;
    this.#log = log;
    this.#unsubscribe = transport.onLine((line) => this.#onLine(line));
  }

  /** Last board identity seen (boot announcement or any hello/get/set reply). */
  get board(): BoardInfo | undefined {
    return this.#board;
  }

  get version(): ProvVersion | undefined {
    return this.#board?.prov;
  }

  onBoard(listener: (b: BoardInfo) => void): () => void {
    this.#boardListeners.add(listener);
    return () => this.#boardListeners.delete(listener);
  }

  /** Forget the board (e.g. after a reconnect to a different device). */
  forgetBoard(): void {
    this.#board = undefined;
  }

  dispose(): void {
    this.#disposed = true;
    this.#unsubscribe();
    for (const p of this.#pending) {
      timers.clearTimeout(p.timer);
      p.reject(new ProvError(p.op, 'client closed'));
    }
    this.#pending = [];
  }

  // ------------------------------------------------------------------ incoming
  #onLine(line: string): void {
    const parsed = parseBoardLine(line);
    if (!parsed) return;
    const { json, crc } = parsed;
    if (crc === 'mismatch') {
      this.#log('board reply failed its CRC check (garbled on the wire) - it will be retried', 'warn');
      return;
    }
    const op = json['op'] as string;
    if (op === 'hello' || op === 'get' || op === 'set') {
      if (json['ok'] !== false && (typeof json['device_id'] === 'string' || typeof json['hwid'] === 'string')) {
        this.#board = normalizeBoard(json);
        for (const l of this.#boardListeners) l(this.#board);
      }
    }
    const id = typeof json['id'] === 'number' ? json['id'] : undefined;
    let idx = id !== undefined ? this.#pending.findIndex((p) => p.id === id) : -1;
    if (idx < 0) idx = this.#pending.findIndex((p) => p.op === op || (p.op === 'get' && op === 'hello'));
    if (idx < 0) return; // unsolicited (boot announcement) - already folded into #board
    const [p] = this.#pending.splice(idx, 1);
    if (!p) return;
    timers.clearTimeout(p.timer);
    p.resolve(json);
  }

  // ------------------------------------------------------------------ outgoing
  async request(op: ProvOp, fields: Readonly<Record<string, unknown>> = {}, opts: RequestOptions = {}): Promise<Readonly<Record<string, unknown>>> {
    const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT[op];
    const retries = opts.retries ?? (IDEMPOTENT.has(op) ? 1 : 0);
    let last: Error | undefined;
    for (let attempt = 0; attempt <= retries; attempt++) {
      if (this.#disposed) throw new ProvError(op, 'client closed');
      try {
        const json = await this.#once(op, fields, timeoutMs);
        if (json['ok'] === false) {
          const field = typeof json['field'] === 'string' ? json['field'] : undefined;
          throw new ProvError(op, typeof json['error'] === 'string' ? json['error'] : `${op} failed`, field, json);
        }
        return json;
      } catch (e) {
        last = e as Error;
        if (!(e instanceof ProvTimeoutError) || attempt === retries) break;
        this.#log(`${last.message} - retrying (${attempt + 1}/${retries})`, 'warn');
        await sleep(250);
      }
    }
    throw last ?? new ProvError(op, 'request failed');
  }

  async #once(op: ProvOp, fields: Readonly<Record<string, unknown>>, timeoutMs: number): Promise<Readonly<Record<string, unknown>>> {
    // hello always goes out in the prov/1 envelope (no "v", no id, no CRC): fw 2.x
    // ignores CRC-suffixed lines, and after a firmware DOWNGRADE the client may still
    // believe it talks to prov/2. fw 3 answers a prov/1 hello with a CRC'd prov/2 reply,
    // and every later request uses prov/2.
    const v2 = op !== 'hello' && this.#board?.prov === 2;
    const id = this.#nextId++ >>> 0;
    const envelope = v2 ? { op, v: 2 as const, id, ...fields } : { op, ...fields };
    const line = formatRequest(envelope, { crc: v2 });
    const reply = new Promise<Readonly<Record<string, unknown>>>((resolve, reject) => {
      const timer = timers.setTimeout(() => {
        this.#pending = this.#pending.filter((p) => p.timer !== timer);
        reject(new ProvTimeoutError(op, timeoutMs));
      }, timeoutMs);
      this.#pending.push({ op, id, resolve, reject, timer });
    });
    try {
      await this.#transport.write(line);
    } catch (e) {
      const i = this.#pending.findIndex((p) => p.id === id);
      const p = this.#pending[i];
      if (p) {
        timers.clearTimeout(p.timer);
        this.#pending.splice(i, 1);
      }
      reply.catch(() => undefined);
      throw new ProvError(op, `could not write to the board: ${(e as Error).message}`);
    }
    this.#log(JSON.stringify(redact({ ionity: 'prov', ...envelope })), 'tx');
    return reply;
  }

  // ------------------------------------------------------------------ operations
  /**
   * Wait until the board identifies itself: a boot announcement or an answer to
   * our hello. Keeps asking while the board boots (or while a native-USB port
   * re-enumerates and writes fail).
   */
  async handshake(totalMs = 15_000): Promise<BoardInfo> {
    const until = Date.now() + totalMs;
    const seen = this.#board;
    let lastErr: Error | undefined;
    while (Date.now() < until) {
      if (this.#board && this.#board !== seen) return this.#board;
      try {
        await this.request('hello', {}, { timeoutMs: Math.min(2500, Math.max(500, until - Date.now())), retries: 0 });
        if (this.#board) return this.#board;
      } catch (e) {
        lastErr = e as Error;
        await sleep(300);
      }
    }
    throw new ProvError('hello', `${lastErr?.message ?? 'no reply'}. Is the firmware 2.0 or newer? On native-USB boards press RESET once and try again.`);
  }

  async get(): Promise<BoardInfo> {
    const json = await this.request('get');
    return normalizeBoard(json);
  }

  /** Writes NVS on the board. Resolves with the fields the board reports as changed. */
  async set(fields: SetFields): Promise<{ changed: string[]; rebootRequired: boolean; board: BoardInfo }> {
    const json = await this.request('set', fields as Readonly<Record<string, unknown>>);
    const changed = Array.isArray(json['changed']) ? (json['changed'] as unknown[]).filter((x): x is string => typeof x === 'string') : [];
    return { changed, rebootRequired: json['reboot_required'] === true, board: normalizeBoard(json) };
  }

  async scan(): Promise<Network[]> {
    return normalizeNetworks(await this.request('scan'));
  }

  /** Join Wi-Fi and reach the host without rebooting. A failed join resolves with ok:false. */
  async test(timeoutMs = 20_000): Promise<TestResult> {
    try {
      return normalizeTest(await this.request('test', { timeout_ms: timeoutMs }, { timeoutMs: timeoutMs + 6000, retries: 0 }));
    } catch (e) {
      if (e instanceof ProvError && e.reply) return normalizeTest(e.reply);
      throw e;
    }
  }

  async reboot(): Promise<void> {
    await this.request('reboot').catch(() => undefined); // the reply may be lost in the reset
    this.#board = undefined;
  }

  async factoryReset(): Promise<void> {
    await this.request('factory_reset');
    this.#board = undefined;
  }
}
