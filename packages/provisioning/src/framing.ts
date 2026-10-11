/*
 * AEDI - IONITY GLOBAL | @ionity/prov - ionity-prov/2 | framing.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Line framing, shared with the boards already in the field (fw 2.x):
 *
 *   host  -> board   {"ionity":"prov","op":"set",...}\n
 *   board -> host    IONITY-PROV {"ok":true,"op":"set",...}\n
 *
 * prov/2 adds, once both sides know the board speaks it (hello reply has "prov":2):
 *   - "v":2 and a numeric "id" in every request, echoed in the reply;
 *   - a CRC-32 suffix on both directions:   {...json...} *1a2b3c4d
 * Exception: "hello" always goes out in the plain prov/1 form (fw 2.x ignores a
 * CRC-suffixed line, and after a firmware downgrade the host cannot know yet).
 * A fw 3 board answers every reply - including that hello - with the CRC suffix.
 *
 * Why the CRC: fw 3 is multi-task. A log line printed by another task can
 * land inside a reply on the same UART/USB-CDC; the suffix turns a silently
 * corrupted reply into a detectable one (the client retries idempotent ops).
 * Everything that is not a prefixed line is ordinary log output.
 */
import { crc32Hex } from './crc32.ts';

export const PROV_PREFIX = 'IONITY-PROV ';

/** fw 2.x: provBuf[768] keeps 767 characters, a longer line is dropped without a reply. */
export const PROV_V1_LINE_MAX = 767;
/** fw 3 advertises its own limit in hello ("line_max"); this is the default it must support. */
export const PROV_V2_LINE_MAX = 4096;

export type ProvVersion = 1 | 2;
export type CrcState = 'ok' | 'absent' | 'mismatch';

export interface BoardLine {
  readonly json: Readonly<Record<string, unknown>>;
  readonly crc: CrcState;
}

const CRC_SUFFIX = / \*([0-9a-f]{8})$/;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Parse one line of board output. Returns null for ordinary log lines and for unusable replies. */
export function parseBoardLine(line: string): BoardLine | null {
  const at = line.indexOf(PROV_PREFIX);
  if (at < 0) return null;
  let body = line.slice(at + PROV_PREFIX.length).trimEnd();
  let crc: CrcState = 'absent';
  const m = CRC_SUFFIX.exec(body);
  if (m) {
    const text = body.slice(0, m.index);
    crc = crc32Hex(text) === m[1] ? 'ok' : 'mismatch';
    body = text;
  }
  let json: unknown;
  try {
    json = JSON.parse(body);
  } catch {
    return null; // interleaved or truncated - the request will time out and retry
  }
  if (!isRecord(json) || typeof json['op'] !== 'string') return null;
  return { json, crc };
}

export interface RequestEnvelope {
  readonly op: string;
  /** prov/2 only */
  readonly v?: ProvVersion;
  readonly id?: number;
  readonly [field: string]: unknown;
}

/** Serialise a request line (newline included). */
export function formatRequest(req: RequestEnvelope, opts: { readonly crc: boolean }): string {
  const text = JSON.stringify({ ionity: 'prov', ...req });
  return opts.crc ? `${text} *${crc32Hex(text)}\n` : `${text}\n`;
}
