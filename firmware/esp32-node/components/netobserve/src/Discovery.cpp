/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/Discovery.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "netobserve/Discovery.h"

#include <string.h>

#include "netobserve/DnsWire.h"

namespace netobs {

int parseMdns(const uint8_t* p, size_t n, MdnsHostFn fn, void* user) {
    if (p == nullptr || n < kDnsHeaderLen || fn == nullptr) return 0;
    const uint16_t qd = rd16(p + 4);
    const uint32_t records = static_cast<uint32_t>(rd16(p + 6)) + rd16(p + 8) + rd16(p + 10);
    if (qd > 64 || records > 128) return 0;
    size_t off = kDnsHeaderLen;
    for (uint16_t i = 0; i < qd; ++i) {
        off = skipName(p, n, off);
        if (off == 0 || off + 4 > n) return 0;
        off += 4;
    }
    int found = 0;
    for (uint32_t i = 0; i < records; ++i) {
        char name[kMaxNameLen + 1];
        uint8_t nameLen = 0;
        size_t next = 0;
        if (!readName(p, n, off, name, sizeof name, nameLen, next) || next + 10 > n) return found;
        const uint16_t type = rd16(p + next);
        const uint16_t rdlen = rd16(p + next + 8);
        const size_t rdata = next + 10;
        if (rdata + rdlen > n) return found;
        static constexpr char kLocal[] = ".local";
        constexpr size_t kLocalLen = sizeof kLocal - 1;
        if (type == 1 && rdlen == 4 && nameLen > kLocalLen && memcmp(name + nameLen - kLocalLen, kLocal, kLocalLen) == 0) {
            const uint32_t ip = rd32(p + rdata);
            const uint32_t a = ip >> 24;
            if (ip != 0 && a != 127 && a != 169 && a < 224) {  // never loopback, link-local or multicast
                fn(user, ip, name, nameLen - kLocalLen);
                ++found;
            }
        }
        off = rdata + rdlen;
    }
    return found;
}

bool parseDhcpRequest(const uint8_t* p, size_t n, DhcpInfo& out) {
    constexpr size_t kCookie = 236;
    if (p == nullptr || n < kCookie + 4) return false;
    if (p[0] != 1 || p[1] != 1 || p[2] != 6) return false;  // BOOTREQUEST, Ethernet, 6-byte MAC
    if (!(p[kCookie] == 0x63 && p[kCookie + 1] == 0x82 && p[kCookie + 2] == 0x53 && p[kCookie + 3] == 0x63)) {
        return false;
    }
    out = DhcpInfo{};
    memcpy(out.mac, p + 28, 6);
    if ((out.mac[0] & 0x01) != 0) return false;  // a multicast MAC cannot be a client
    out.ip = rd32(p + 12);                       // ciaddr (RENEW / INFORM)
    uint32_t requested = 0;
    size_t off = kCookie + 4;
    while (off < n) {
        const uint8_t code = p[off];
        if (code == 0) {  // pad
            ++off;
            continue;
        }
        if (code == 255) break;
        if (off + 2 > n) return false;
        const uint8_t len = p[off + 1];
        const uint8_t* v = p + off + 2;
        if (off + 2 + len > n) return false;
        if (code == 53 && len == 1) out.msgType = v[0];
        if (code == 50 && len == 4) requested = rd32(v);
        if (code == 12 && len > 0) {
            const size_t m = len < sizeof out.hostname - 1 ? len : sizeof out.hostname - 1;
            memcpy(out.hostname, v, m);
            out.hostname[m] = '\0';
            out.hostnameLen = static_cast<uint8_t>(strnlen(out.hostname, m));
        }
        off += 2u + len;
    }
    if (out.ip == 0) out.ip = requested;
    const uint32_t a = out.ip >> 24;
    if (a == 0 || a == 127 || a >= 224) out.ip = 0;
    return out.msgType != 0;
}

}  // namespace netobs
