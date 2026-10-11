/*
 * AEDI - IONITY GLOBAL | @ionity/prov - ionity-prov/2 | uuid.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Device UUIDs (RFC 9562). New boards get a UUIDv7: time-ordered, so the
 * fleet registry's primary-key index stays append-mostly. The UUID is the
 * logical asset id - when a board is swapped, the replacement is provisioned
 * with the old UUID and keeps the asset's history; the hwid (MAC) changes.
 */

export type RandomFill = (bytes: Uint8Array) => void;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function defaultRandom(bytes: Uint8Array): void {
  const c = (globalThis as { crypto?: { getRandomValues(a: Uint8Array): Uint8Array } }).crypto;
  if (!c) throw new Error('no crypto.getRandomValues in this runtime');
  c.getRandomValues(bytes);
}

/** Canonical form: lowercase, hyphenated. Accepts upper case and surrounding braces/whitespace. */
export function normalizeUuid(input: string): string | null {
  const s = input.trim().replace(/^\{(.*)\}$/, '$1').toLowerCase();
  return UUID_RE.test(s) ? s : null;
}

export function isUuid(input: string): boolean {
  return normalizeUuid(input) !== null;
}

/** RFC 9562 section 5.7: 48-bit unix ms, version 7, 74 random bits, variant 10. */
export function uuidv7(nowMs: number = Date.now(), random: RandomFill = defaultRandom): string {
  if (!Number.isSafeInteger(nowMs) || nowMs < 0 || nowMs > 0xffff_ffff_ffff) throw new RangeError('uuidv7: timestamp out of range');
  const b = new Uint8Array(16);
  random(b);
  let t = nowMs;
  for (let i = 5; i >= 0; i--) {
    b[i] = t % 256;
    t = Math.floor(t / 256);
  }
  b[6] = 0x70 | ((b[6] ?? 0) & 0x0f);
  b[8] = 0x80 | ((b[8] ?? 0) & 0x3f);
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** Milliseconds encoded in a UUIDv7, or null for other versions. */
export function uuidv7Time(uuid: string): number | null {
  const n = normalizeUuid(uuid);
  if (!n || n[14] !== '7') return null;
  return parseInt(n.slice(0, 8) + n.slice(9, 13), 16);
}
