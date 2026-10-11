/*
 * AEDI - IONITY GLOBAL | @ionity/prov - ionity-prov/2 | text.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * UTF-8 helpers without TextEncoder, so the package runs unchanged in the
 * browser, in Node and in tests without DOM or Node type dependencies.
 * Firmware buffers are sized in BYTES; every limit in this package is too.
 */

export function utf8Encode(s: string): Uint8Array {
  const out: number[] = [];
  for (const ch of s) {
    const cp = ch.codePointAt(0) ?? 0;
    if (cp < 0x80) out.push(cp);
    else if (cp < 0x800) out.push(0xc0 | (cp >> 6), 0x80 | (cp & 0x3f));
    else if (cp < 0x1_0000) out.push(0xe0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
    else out.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3f), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
  }
  return Uint8Array.from(out);
}

export function utf8ByteLength(s: string): number {
  let n = 0;
  for (const ch of s) {
    const cp = ch.codePointAt(0) ?? 0;
    n += cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x1_0000 ? 3 : 4;
  }
  return n;
}

/** True when every character is printable ASCII (0x20..0x7E) - the WPA2 passphrase alphabet. */
export function isPrintableAscii(s: string): boolean {
  return /^[\x20-\x7e]*$/.test(s);
}

/** C0/C1 control characters (and DEL) break NVS strings, logs and MQTT topic levels. */
export function hasControlChars(s: string): boolean {
  return /[\u0000-\u001f\u007f-\u009f]/.test(s);
}
