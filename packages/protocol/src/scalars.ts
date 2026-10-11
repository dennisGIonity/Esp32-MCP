/*
 * AEDI - IONITY GLOBAL | @ionity/ewp - Edge Wire Protocol v1 | scalars.ts
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Branded wire scalars.
 *
 * A plain `number` or `string` can never be assigned to a wire field: it must
 * pass through one of the range-checked constructors below first. This is the
 * TypeScript half of the firmware's memory-safety contract - every value that
 * reaches the ESP32 has already been proven to fit the fixed-width C type (or
 * the fixed-size buffer) the firmware decodes it into.
 *
 * MEMORY-SAFETY NOTES
 *  - Strings are bounded in UTF-8 BYTES, not JS characters. "Ü" is 1 JS char
 *    but 2 bytes; the firmware's `char buf[N]` only cares about bytes.
 *  - Embedded NUL (U+0000) is rejected: C string handling on the device would
 *    silently truncate at the NUL and two different strings would collide.
 *  - Floats must be finite. CBOR can encode NaN/±Infinity; JSON (and therefore
 *    MCP structuredContent) cannot, and a NaN duty cycle on an LEDC channel is
 *    undefined behaviour in the actuator driver.
 */

declare const brand: unique symbol;

/** Nominal typing helper: `Brand<number, 'u8'>` is not assignable from `number`. */
export type Brand<T, B extends string> = T & { readonly [brand]: B };

export type U8 = Brand<number, 'u8'>;
export type U16 = Brand<number, 'u16'>;
export type U32 = Brand<number, 'u32'>;
export type I8 = Brand<number, 'i8'>;
export type I32 = Brand<number, 'i32'>;
/**
 * IEEE-754 binary32 value that is finite (already rounded with Math.fround).
 * DECODER RULE (firmware + gateway): a JS encoder cannot tell 1.0 from 1, so a
 * float field may arrive as a CBOR integer (0, 1, 3 ...) - accept both major
 * type 0/1 and 7 for every F32/Unit field.
 */
export type F32 = Brand<number, 'f32'>;
/** Finite binary32 in the closed range [0, 1] - duty cycles, probabilities. */
export type Unit = Brand<number, 'unit'>;

/** Byte string of at most N bytes (CBOR major type 2). */
export type Bytes<N extends number> = Brand<Uint8Array, `bytes<=${N}`>;
/** Byte string of exactly N bytes - hashes, UUIDs, addresses. */
export type FixedBytes<N extends number> = Brand<Uint8Array, `bytes==${N}`>;
/** UTF-8 text of at most N encoded bytes, no NUL (CBOR major type 3). */
export type Utf8<N extends number> = Brand<string, `utf8<=${N}`>;

function int(n: number, min: number, max: number, t: string): number {
  if (!Number.isInteger(n) || n < min || n > max) {
    throw new RangeError(`${t}: ${String(n)} is not an integer in [${min}, ${max}]`);
  }
  return n;
}

export const u8 = (n: number): U8 => int(n, 0, 0xff, 'u8') as U8;
export const u16 = (n: number): U16 => int(n, 0, 0xffff, 'u16') as U16;
export const u32 = (n: number): U32 => int(n, 0, 0xffff_ffff, 'u32') as U32;
export const i8 = (n: number): I8 => int(n, -0x80, 0x7f, 'i8') as I8;
export const i32 = (n: number): I32 => int(n, -0x8000_0000, 0x7fff_ffff, 'i32') as I32;

export const f32 = (n: number): F32 => {
  if (!Number.isFinite(n)) throw new RangeError(`f32: ${String(n)} is not finite`);
  const r = Math.fround(n);
  if (!Number.isFinite(r)) throw new RangeError(`f32: ${String(n)} overflows binary32`);
  return r as F32;
};

export const unit = (n: number): Unit => {
  const v: number = f32(n);
  if (v < 0 || v > 1) throw new RangeError(`unit: ${String(n)} is not in [0, 1]`);
  return v as Unit;
};

export function bytes<N extends number>(b: Uint8Array, max: N): Bytes<N> {
  if (b.byteLength > max) throw new RangeError(`bytes: ${b.byteLength} B exceeds ${max} B`);
  return b as Bytes<N>;
}

export function fixedBytes<N extends number>(b: Uint8Array, len: N): FixedBytes<N> {
  if (b.byteLength !== len) throw new RangeError(`bytes: expected exactly ${len} B, got ${b.byteLength} B`);
  return b as FixedBytes<N>;
}

/** UTF-8 encoded length without TextEncoder, so the package has no DOM/Node type dependency. */
export function utf8ByteLength(s: string): number {
  let n = 0;
  for (const ch of s) {
    const cp = ch.codePointAt(0) ?? 0;
    n += cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x1_0000 ? 3 : 4;
  }
  return n;
}

export function utf8<N extends number>(s: string, maxBytes: N): Utf8<N> {
  if (s.includes('\u0000')) throw new RangeError('utf8: embedded NUL is not allowed on the wire');
  if (!s.isWellFormed()) throw new RangeError('utf8: lone surrogate - string is not well-formed UTF-16');
  const n = utf8ByteLength(s);
  if (n > maxBytes) throw new RangeError(`utf8: ${n} B exceeds ${maxBytes} B`);
  return s as Utf8<N>;
}
