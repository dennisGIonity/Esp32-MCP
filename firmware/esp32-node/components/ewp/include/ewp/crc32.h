/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | ewp/crc32.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * CRC-32/ISO-HDLC (zlib / Ethernet: reflected polynomial 0xEDB88320, init and
 * final XOR 0xFFFFFFFF). Check value: crc32("123456789") == 0xCBF43926.
 *
 * Used by the ionity-prov/2 serial protocol (" *1a2b3c4d" line suffix). It is
 * byte-for-byte the algorithm of packages/provisioning/src/crc32.ts.
 *
 * We carry our own 1 KiB table (in flash, .rodata) instead of the ROM routine
 * esp_rom_crc32_le(): the same code then runs in the host unit tests, and the
 * check value is proven by test_crc32 on both sides of the wire.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace ewp {

/** Continue a CRC over more bytes: crc32_update(crc32_update(0, a), b) == crc32(a ++ b). */
uint32_t crc32_update(uint32_t crc, const void* data, size_t len);

/** One-shot CRC-32/ISO-HDLC of `len` bytes. `data` may be nullptr only when len == 0. */
inline uint32_t crc32(const void* data, size_t len) { return crc32_update(0, data, len); }

/**
 * Write the CRC as exactly 8 lowercase hex digits plus a NUL terminator.
 * MEMORY-SAFETY: `out` must have room for 9 bytes.
 */
void crc32_hex(uint32_t crc, char out[9]);

/**
 * Parse exactly 8 lowercase hex digits (the wire form). Uppercase is rejected
 * on purpose: the host always sends lowercase, so anything else is corruption.
 */
bool crc32_parse_hex(const char* text8, uint32_t* out);

}  // namespace ewp
