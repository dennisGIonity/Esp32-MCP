/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/DnsWire.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "netobserve/DnsWire.h"

#include <string.h>

namespace netobs {
namespace {

char lowerPrintable(uint8_t c, bool& clean) {
    if (c >= 'A' && c <= 'Z') return static_cast<char>(c + 32);
    if (c == '.' || c < 0x21 || c > 0x7E) {
        clean = false;  // a dot inside a label, a space, a control byte or non-ASCII
        return '?';
    }
    return static_cast<char>(c);
}

/** Append one label to out (with a separating dot). False when it would exceed kMaxNameLen. */
bool appendLabel(const uint8_t* label, size_t len, char* out, size_t outCap, size_t& outLen, bool& clean) {
    const size_t need = (outLen > 0 ? 1 : 0) + len;
    if (outLen + need > kMaxNameLen || outLen + need + 1 > outCap) return false;
    if (outLen > 0) out[outLen++] = '.';
    for (size_t i = 0; i < len; ++i) out[outLen++] = lowerPrintable(label[i], clean);
    out[outLen] = '\0';
    return true;
}

/** Offset after one resource record starting at off (name, fixed part, rdata), 0 if malformed. */
size_t skipRecord(const uint8_t* p, size_t n, size_t off, uint16_t* type, uint32_t* ttlOff) {
    off = skipName(p, n, off);
    if (off == 0 || off + 10 > n) return 0;
    if (type != nullptr) *type = rd16(p + off);
    if (ttlOff != nullptr) *ttlOff = static_cast<uint32_t>(off + 4);
    const size_t rdlen = rd16(p + off + 8);
    off += 10;
    if (off + rdlen > n) return 0;
    return off + rdlen;
}

}  // namespace

size_t skipName(const uint8_t* p, size_t n, size_t off) {
    size_t total = 0;
    while (off < n) {
        const uint8_t len = p[off];
        if ((len & 0xC0) == 0xC0) return off + 2 <= n ? off + 2 : 0;  // a pointer ends the name in this record
        if ((len & 0xC0) != 0) return 0;                              // 0x40 / 0x80: reserved label types
        if (len == 0) return off + 1;
        total += len + 1u;
        if (total > 255) return 0;
        off += 1u + len;
    }
    return 0;
}

bool readName(const uint8_t* p, size_t n, size_t off, char* out, size_t outCap, uint8_t& outLen, size_t& next) {
    size_t len = 0;
    bool clean = true;
    bool jumped = false;
    int hops = 0;
    next = 0;
    if (outCap == 0) return false;
    out[0] = '\0';
    for (;;) {
        if (off >= n) return false;
        const uint8_t l = p[off];
        if ((l & 0xC0) == 0xC0) {
            if (off + 2 > n || ++hops > kMaxPointerHops) return false;
            if (!jumped) next = off + 2;
            jumped = true;
            const size_t target = static_cast<size_t>(((l & 0x3F) << 8) | p[off + 1]);
            if (target >= off) return false;  // pointers must point backwards: no loops, no forward games
            off = target;
            continue;
        }
        if ((l & 0xC0) != 0) return false;
        if (l == 0) {
            if (!jumped) next = off + 1;
            break;
        }
        if (off + 1 + l > n) return false;
        if (!appendLabel(p + off + 1, l, out, outCap, len, clean)) return false;
        off += 1u + l;
    }
    outLen = static_cast<uint8_t>(len);
    return true;
}

bool parseQuery(const uint8_t* p, size_t n, DnsQuestion& q) {
    if (p == nullptr || n < kDnsHeaderLen + 5 || n > 4096) return false;
    if ((p[2] & 0x80) != 0) return false;           // a response, not a query
    if (((p[2] >> 3) & 0x0F) != 0) return false;    // only standard QUERY
    if (rd16(p + 4) != 1) return false;             // exactly one question
    q.nameClean = true;
    size_t len = 0;
    size_t off = kDnsHeaderLen;
    q.name[0] = '\0';
    for (;;) {
        if (off >= n) return false;
        const uint8_t l = p[off];
        if (l == 0) {
            ++off;
            break;
        }
        if ((l & 0xC0) != 0) return false;  // no compression (or reserved types) in a question
        if (off + 1 + l > n) return false;
        if (!appendLabel(p + off + 1, l, q.name, sizeof q.name, len, q.nameClean)) return false;
        off += 1u + l;
    }
    if (off + 4 > n) return false;
    q.nameLen = static_cast<uint8_t>(len);
    q.nameWireLen = static_cast<uint16_t>(off - kDnsHeaderLen);
    q.qtype = rd16(p + off);
    q.qclass = rd16(p + off + 2);
    q.end = static_cast<uint16_t>(off + 4);
    // EDNS: an OPT record in the additional section (answer/authority are empty in a query).
    q.edns = false;
    const uint16_t an = rd16(p + 6);
    const uint16_t ns = rd16(p + 8);
    const uint16_t ar = rd16(p + 10);
    size_t r = q.end;
    const uint32_t records = static_cast<uint32_t>(an) + ns + ar;
    if (records > 16) return false;  // a query with more records than this is not a client query
    for (uint32_t i = 0; i < records; ++i) {
        uint16_t type = 0;
        r = skipRecord(p, n, r, &type, nullptr);
        if (r == 0) return false;
        if (type == kTypeOpt) q.edns = true;
    }
    return true;
}

bool parseResponse(const uint8_t* p, size_t n, const DnsQuestion& expect, DnsAnswerInfo& info) {
    if (p == nullptr || n < kDnsHeaderLen || (p[2] & 0x80) == 0) return false;
    info.rcode = static_cast<uint8_t>(p[3] & 0x0F);
    info.truncated = (p[2] & 0x02) != 0;
    const uint16_t qd = rd16(p + 4);
    info.ancount = rd16(p + 6);
    const uint16_t ns = rd16(p + 8);
    const uint16_t ar = rd16(p + 10);
    info.minTtl = 0;
    if (qd != 1) return false;
    char name[kMaxNameLen + 1];
    uint8_t nameLen = 0;
    size_t off = 0;
    if (!readName(p, n, kDnsHeaderLen, name, sizeof name, nameLen, off) || off + 4 > n) return false;
    if (nameLen != expect.nameLen || memcmp(name, expect.name, nameLen) != 0) return false;
    if (rd16(p + off) != expect.qtype || rd16(p + off + 2) != expect.qclass) return false;
    off += 4;
    bool first = true;
    const uint32_t records = static_cast<uint32_t>(info.ancount) + ns + ar;
    for (uint32_t i = 0; i < records; ++i) {
        uint16_t type = 0;
        uint32_t ttlOff = 0;
        const size_t next = skipRecord(p, n, off, &type, &ttlOff);
        if (next == 0) return false;
        if (i < info.ancount && type != kTypeOpt) {
            const uint32_t ttl = rd32(p + ttlOff);
            if (first || ttl < info.minTtl) info.minTtl = ttl;
            first = false;
        }
        off = next;
    }
    return true;
}

size_t buildServfail(const uint8_t* query, const DnsQuestion& q, uint8_t* out, size_t cap) {
    if (q.end > cap) return 0;
    memcpy(out, query, q.end);
    out[2] = static_cast<uint8_t>(0x80 | (query[2] & 0x79));  // QR, keep OPCODE + RD, clear AA/TC
    out[3] = 0x80 | 0x02;                                      // RA, RCODE SERVFAIL
    wr16(out + 6, 0);
    wr16(out + 8, 0);
    wr16(out + 10, 0);
    return q.end;
}

bool ageTtls(uint8_t* p, size_t n, uint32_t elapsedS) {
    if (n < kDnsHeaderLen) return false;
    const uint16_t qd = rd16(p + 4);
    size_t off = kDnsHeaderLen;
    for (uint16_t i = 0; i < qd; ++i) {
        off = skipName(p, n, off);
        if (off == 0 || off + 4 > n) return false;
        off += 4;
    }
    const uint32_t records = static_cast<uint32_t>(rd16(p + 6)) + rd16(p + 8) + rd16(p + 10);
    for (uint32_t i = 0; i < records; ++i) {
        uint16_t type = 0;
        uint32_t ttlOff = 0;
        const size_t next = skipRecord(p, n, off, &type, &ttlOff);
        if (next == 0) return false;
        if (type != kTypeOpt) {
            const uint32_t ttl = rd32(p + ttlOff);
            wr32(p + ttlOff, ttl > elapsedS ? ttl - elapsedS : 0);
        }
        off = next;
    }
    return true;
}

}  // namespace netobs
