/*
 * AEDI - IONITY GLOBAL | @ionity/ewp - Edge Wire Protocol v1 | keys.ts
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Integer CBOR map keys, checked by the compiler.
 *
 * Bodies travel as CBOR maps with small unsigned-integer keys (the COSE/CWT
 * idiom): `{1: 3, 2: {...}}` instead of `{"model": 3, "input": {...}}`.
 * Keys 0..23 encode in ONE byte, so `CborKey` is restricted to that range -
 * a body that needs more than 24 fields must nest.
 *
 * `defineKeys<T>()(table)` fails to COMPILE when the table
 *   - misses a field of T (including optional fields and every union variant),
 *   - names a field T does not have,
 *   - assigns the same key to two fields.
 * Those are exactly the drift bugs that corrupt a binary protocol silently.
 */

// prettier-ignore
export type CborKey =
  | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11
  | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23;

/** keyof distributed over a union: every field of every variant. */
export type AllKeys<T> = T extends unknown ? keyof T : never;

export type KeyTable<T> = { readonly [P in AllKeys<T>]-?: CborKey };

type DuplicateFields<K> = {
  [P in keyof K]: K[P] extends K[Exclude<keyof K, P>] ? P : never;
}[keyof K];

type NoExtraFields<T, K> = { readonly [P in Exclude<keyof K, AllKeys<T>>]: never };

type NoDuplicates<K> = [DuplicateFields<K>] extends [never]
  ? unknown
  : { readonly __duplicateCborKeys: DuplicateFields<K> };

export function defineKeys<T>() {
  return <const K extends KeyTable<T>>(table: K & NoExtraFields<T, K> & NoDuplicates<K>): K => table;
}
