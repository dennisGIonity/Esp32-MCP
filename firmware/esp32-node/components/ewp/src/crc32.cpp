/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | ewp/crc32.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "ewp/crc32.h"

namespace ewp {
namespace {

// The table is computed at compile time and lands in flash (.rodata): no
// RAM cost and no "first call" initialisation race between tasks.
struct Crc32Table {
    uint32_t v[256];
    constexpr Crc32Table() : v() {
        for (uint32_t n = 0; n < 256; ++n) {
            uint32_t c = n;
            for (int k = 0; k < 8; ++k) c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
            v[n] = c;
        }
    }
};
constexpr Crc32Table kTable{};

constexpr char kHex[] = "0123456789abcdef";

}  // namespace

uint32_t crc32_update(uint32_t crc, const void* data, size_t len) {
    const uint8_t* p = static_cast<const uint8_t*>(data);
    uint32_t c = ~crc;  // undo the final XOR of the previous call (and apply the 0xFFFFFFFF init)
    for (size_t i = 0; i < len; ++i) c = kTable.v[(c ^ p[i]) & 0xFFu] ^ (c >> 8);
    return ~c;
}

void crc32_hex(uint32_t crc, char out[9]) {
    for (int i = 7; i >= 0; --i) {
        out[i] = kHex[crc & 0xFu];
        crc >>= 4;
    }
    out[8] = '\0';
}

bool crc32_parse_hex(const char* text8, uint32_t* out) {
    uint32_t v = 0;
    for (int i = 0; i < 8; ++i) {
        const char ch = text8[i];
        uint32_t d;
        if (ch >= '0' && ch <= '9') {
            d = static_cast<uint32_t>(ch - '0');
        } else if (ch >= 'a' && ch <= 'f') {
            d = static_cast<uint32_t>(ch - 'a' + 10);
        } else {
            return false;
        }
        v = (v << 4) | d;
    }
    *out = v;
    return true;
}

}  // namespace ewp
