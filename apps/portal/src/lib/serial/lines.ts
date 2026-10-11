/*
 * AEDI - IONITY GLOBAL | @ionity/portal | lines.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Serial bytes -> text lines. USB delivers arbitrary chunks: a line (or a
 * multi-byte UTF-8 character) can be split across reads, so decoding is
 * streaming and the remainder is carried to the next chunk.
 */

const ANSI = /\x1b\[[0-9;?]*[ -/]*[@-~]/g;

export function stripAnsi(s: string): string {
  return s.replace(ANSI, '');
}

export class LineSplitter {
  readonly #decoder = new TextDecoder('utf-8');
  readonly #maxLine: number;
  #buf = '';

  /** @param maxLine a "line" this long without a newline is flushed as-is (baud mismatch / binary noise). */
  constructor(maxLine = 16 * 1024) {
    this.#maxLine = maxLine;
  }

  push(chunk: Uint8Array): string[] {
    this.#buf += this.#decoder.decode(chunk, { stream: true });
    const out: string[] = [];
    let start = 0;
    for (let nl = this.#buf.indexOf('\n'); nl >= 0; nl = this.#buf.indexOf('\n', start)) {
      out.push(this.#buf.slice(start, nl).replace(/\r$/, ''));
      start = nl + 1;
    }
    this.#buf = this.#buf.slice(start);
    if (this.#buf.length > this.#maxLine) {
      out.push(this.#buf);
      this.#buf = '';
    }
    return out;
  }

  /** Text received after the last newline (e.g. a prompt). */
  get pending(): string {
    return this.#buf;
  }

  reset(): void {
    this.#buf = '';
    this.#decoder.decode(); // drop a half-received character
  }
}

/** Bounded history of lines, for "Save log" - never holds more than `max` lines. */
export class LineHistory {
  readonly #max: number;
  #lines: string[] = [];

  constructor(max = 20_000) {
    this.#max = max;
  }

  push(line: string): void {
    this.#lines.push(line);
    if (this.#lines.length > this.#max) this.#lines.splice(0, this.#lines.length - this.#max);
  }

  text(): string {
    return this.#lines.map(stripAnsi).join('\n') + '\n';
  }

  clear(): void {
    this.#lines = [];
  }

  get size(): number {
    return this.#lines.length;
  }
}
