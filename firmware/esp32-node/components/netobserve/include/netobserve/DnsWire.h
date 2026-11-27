/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/DnsWire.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The parts of the DNS wire format (RFC 1035, 6891) a forwarder needs. A
 * forwarder never rewrites a message beyond the 16-bit id (and, for cache
 * hits, the question's letter case and the TTLs), so this is a set of
 * bounds-checked readers, not a full codec.
 *
 * MEMORY-SAFETY: every read is checked against the datagram length; names
 * are followed through at most kMaxPointerHops compression pointers; nothing
 * here allocates. Input is hostile by assumption (it arrives from any device
 * on the LAN and from the internet).
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace netobs {

constexpr size_t kDnsHeaderLen = 12;
/** RFC 1035 presentation-form limit (no trailing dot). */
constexpr size_t kMaxNameLen = 253;
constexpr int kMaxPointerHops = 16;
constexpr uint16_t kTypeOpt = 41;
constexpr uint16_t kClassIn = 1;

inline uint16_t rd16(const uint8_t* p) { return static_cast<uint16_t>((p[0] << 8) | p[1]); }
inline uint32_t rd32(const uint8_t* p) {
    return (static_cast<uint32_t>(p[0]) << 24) | (static_cast<uint32_t>(p[1]) << 16) | (static_cast<uint32_t>(p[2]) << 8) | p[3];
}
inline void wr16(uint8_t* p, uint16_t v) {
    p[0] = static_cast<uint8_t>(v >> 8);
    p[1] = static_cast<uint8_t>(v);
}
inline void wr32(uint8_t* p, uint32_t v) {
    p[0] = static_cast<uint8_t>(v >> 24);
    p[1] = static_cast<uint8_t>(v >> 16);
    p[2] = static_cast<uint8_t>(v >> 8);
    p[3] = static_cast<uint8_t>(v);
}

/** The single question of a standard query. */
struct DnsQuestion {
    char name[kMaxNameLen + 1];  // lower-case presentation form, "" = root
    uint8_t nameLen;
    /** false when a label held '.', a control byte or non-ASCII: logged with '?', never cached */
    bool nameClean;
    uint16_t qtype;
    uint16_t qclass;
    /** offset just past the question section (header + name + type + class) */
    uint16_t end;
    /** wire length of the encoded name (labels + root byte) */
    uint16_t nameWireLen;
    /** the query carries an OPT record (EDNS) - part of the cache key */
    bool edns;
};

/**
 * A standard query: QR = 0, OPCODE = 0 (QUERY), exactly one question, an
 * uncompressed name. Returns false for anything else (the forwarder drops it).
 */
bool parseQuery(const uint8_t* p, size_t n, DnsQuestion& q);

struct DnsAnswerInfo {
    uint8_t rcode;
    bool truncated;
    uint16_t ancount;
    /** smallest TTL in the answer section (0 when it has none) */
    uint32_t minTtl;
};

/**
 * A response (QR = 1) whose every record lies inside the datagram. Fills
 * `info` and checks the question against `expect` (same name ignoring case,
 * same type and class) - a reply to some other question is rejected.
 */
bool parseResponse(const uint8_t* p, size_t n, const DnsQuestion& expect, DnsAnswerInfo& info);

/**
 * Decode a (possibly compressed) name at `off` into lower-case presentation
 * form. `next` receives the offset after the name in the record (not after
 * a pointer target). False when it runs out of bounds or loops.
 */
bool readName(const uint8_t* p, size_t n, size_t off, char* out, size_t outCap, uint8_t& outLen, size_t& next);

/** Skip a (possibly compressed) name: offset after it, or 0 on malformed input. */
size_t skipName(const uint8_t* p, size_t n, size_t off);

/**
 * SERVFAIL for `query` (header + question copied, QR/RA set, RCODE 2, no
 * records) into out. Returns its length, 0 when it does not fit.
 */
size_t buildServfail(const uint8_t* query, const DnsQuestion& q, uint8_t* out, size_t cap);

/**
 * Subtract `elapsedS` from every TTL in a response held in a buffer (answer,
 * authority, additional; the OPT pseudo-record's TTL field is not a TTL and
 * is left alone). False when the message is malformed.
 */
bool ageTtls(uint8_t* p, size_t n, uint32_t elapsedS);

}  // namespace netobs
