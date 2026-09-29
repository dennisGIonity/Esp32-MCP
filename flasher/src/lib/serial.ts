// ===========================================================================
// AEDI - IONITY GLOBAL | Line-oriented Web Serial session | Policy 986 AED
// ---------------------------------------------------------------------------
// Used after flashing (esptool-js has closed the port) to talk ionity-prov/1
// and to show the board's boot log.
// ===========================================================================
import { buildRequest, parseProvLine, redact, type BoardInfo, type ProvOp } from "./prov";

export type LogFn = (line: string, kind?: "rx" | "tx" | "info" | "error") => void;

export class LineSerial {
  private reader?: ReadableStreamDefaultReader<Uint8Array>;
  private loop?: Promise<void>;
  private buf = "";
  private closed = false;
  private waiters: { op: string; resolve: (b: BoardInfo) => void; reject: (e: Error) => void; timer: number }[] = [];
  lastHello?: BoardInfo;

  constructor(public port: SerialPort, private log: LogFn) {}

  async open(baud = 115200): Promise<void> {
    if (!this.port.readable) await this.port.open({ baudRate: baud, bufferSize: 4096 });
    // Auto-reset circuits (CH340 / CP210x boards): DTR+RTS de-asserted = run
    // the app, not the ROM bootloader.
    try { await this.port.setSignals({ dataTerminalReady: false, requestToSend: false }); } catch { /* native USB */ }
    this.closed = false;
    this.loop = this.readLoop();
  }

  private async readLoop(): Promise<void> {
    const dec = new TextDecoder();
    while (!this.closed && this.port.readable) {
      this.reader = this.port.readable.getReader();
      try {
        for (;;) {
          const { value, done } = await this.reader.read();
          if (done) break;
          this.buf += dec.decode(value, { stream: true });
          let nl: number;
          while ((nl = this.buf.indexOf("\n")) >= 0) {
            const line = this.buf.slice(0, nl).replace(/\r$/, "");
            this.buf = this.buf.slice(nl + 1);
            this.onLine(line);
          }
          if (this.buf.length > 8192) this.buf = this.buf.slice(-2048);
        }
      } catch (e) {
        // Break / framing / overrun errors happen while the chip resets and are
        // recoverable: take a new reader. Only a closed or vanished port ends the loop.
        if (this.closed || !this.port.readable) break;
        this.log(`serial: ${(e as Error).name} (continuing)`, "info");
      } finally {
        try { this.reader.releaseLock(); } catch { /* ignore */ }
      }
    }
  }

  private onLine(line: string): void {
    const p = parseProvLine(line);
    if (!p) {
      if (line.trim()) this.log(line, "rx");
      return;
    }
    this.log(line.length > 400 ? line.slice(0, 400) + "…" : line, "rx");
    if (p.op === "hello") this.lastHello = p;
    const i = this.waiters.findIndex((w) => w.op === p.op || (w.op === "get" && p.op === "hello"));
    if (i >= 0) {
      const [w] = this.waiters.splice(i, 1);
      clearTimeout(w.timer);
      w.resolve(p);
    }
  }

  async request(op: ProvOp, fields: Record<string, unknown> = {}, timeoutMs = 4000): Promise<BoardInfo> {
    if (!this.port.writable) throw new Error("port is not open");
    const req = buildRequest(op, fields);
    const p = new Promise<BoardInfo>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        this.waiters = this.waiters.filter((w) => w.timer !== timer);
        reject(new Error(`no "${op}" reply from the board in ${Math.round(timeoutMs / 1000)} s`));
      }, timeoutMs);
      this.waiters.push({ op, resolve, reject, timer });
    });
    const w = this.port.writable.getWriter();
    try {
      await w.write(new TextEncoder().encode(req));
    } catch (e) {
      const i = this.waiters.findIndex((x) => x.op === op);
      if (i >= 0) { clearTimeout(this.waiters[i].timer); this.waiters.splice(i, 1); }
      p.catch(() => undefined);
      throw e;
    } finally {
      w.releaseLock();
    }
    this.log(redact(req.trim()), "tx");
    return p;
  }

  /** Wait for the board to be alive: a boot "hello", or an answer to ours. */
  async handshake(totalMs = 12000): Promise<BoardInfo> {
    const until = Date.now() + totalMs;
    let lastErr: Error | undefined;
    while (Date.now() < until) {
      if (this.lastHello) return this.lastHello;
      try {
        return await this.request("hello", {}, 2000);
      } catch (e) {
        lastErr = e as Error;
        await new Promise((r) => setTimeout(r, 250));      // never spin on an instant failure
      }
    }
    throw new Error(
      (lastErr?.message ?? "no reply") +
        ". Is this firmware 2.0+? On native-USB boards press RESET once, then Retry.",
    );
  }

  async setSignalsReset(): Promise<void> {
    // EN low -> high with IO0 high: plain reset into the app (CH340/CP210x boards).
    try {
      await this.port.setSignals({ dataTerminalReady: false, requestToSend: true });
      await new Promise((r) => setTimeout(r, 120));
      await this.port.setSignals({ dataTerminalReady: false, requestToSend: false });
    } catch { /* native USB has no reset lines */ }
  }

  async close(): Promise<void> {
    this.closed = true;
    for (const w of this.waiters) { clearTimeout(w.timer); w.reject(new Error("closed")); }
    this.waiters = [];
    try { await this.reader?.cancel(); } catch { /* ignore */ }
    try { await this.loop; } catch { /* ignore */ }         // reader lock released
    try { await this.port.close(); } catch (e) { this.log(`close: ${(e as Error).message}`, "info"); }
  }
}
