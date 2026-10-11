/*
 * AEDI - IONITY GLOBAL | @ionity/prov - ionity-prov/2 | crc32.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * CRC-32/ISO-HDLC (the zlib / Ethernet CRC, reflected polynomial 0xEDB88320).
 * Check value: crc32("123456789") = 0xCBF43926. The firmware (Phase 3) may use
 * the ROM routine esp_rom_crc32_le(0, buf, len) only if its unit test proves
 * the same check value; otherwise it carries this 256-entry table (1 KiB flash).
 */
import { utf8Encode } from './text.ts';

const TABLE: Uint32Array = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb8_8320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32(bytes: Uint8Array): number {
  let c = 0xffff_ffff;
  for (const b of bytes) c = (TABLE[(c ^ b) & 0xff] ?? 0) ^ (c >>> 8);
  return (c ^ 0xffff_ffff) >>> 0;
}

/** CRC-32 of the UTF-8 bytes of `text`, as 8 lowercase hex digits. */
export function crc32Hex(text: string): string {
  return crc32(utf8Encode(text)).toString(16).padStart(8, '0');
}
