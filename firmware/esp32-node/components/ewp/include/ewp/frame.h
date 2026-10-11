/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | ewp/frame.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The 16-byte EWP/1 frame header and the first, CBOR-free validation stage.
 *
 *   0      1      2      3      4             8      9      10      12      14      16
 *   | 0xE5 | ver  | type |flags |  rid (u32)  | tool |status|deadln |bodyLen|reservd| CBOR body
 *
 * All multi-byte header fields are LITTLE-endian (the CBOR body is big-endian).
 * They are read and written byte by byte: the transport buffer handed to us by
 * esp-mqtt has no alignment guarantee, so it is NEVER cast to
 * ewp_header_wire_t* (an unaligned u32 load faults on Xtensa).
 *
 * checkDownlink() runs in the MQTT task on Core 0 for every downlink frame.
 * It is O(1), allocation-free and never touches the CBOR body - the order of
 * the checks is the contract's (Phase 1, firmware rule 7), cheap first.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "ewp_protocol.h"

namespace ewp {

/** Decoded header fields in host form. magic/version/reserved are implied. */
struct Header {
    uint8_t type = 0;
    uint8_t flags = 0;
    uint32_t rid = 0;
    uint8_t tool = EWP_NO_TOOL;
    uint8_t status = EWP_ST_OK;
    uint16_t deadlineMs = 0;
    uint16_t bodyLen = 0;
};

/** Writes all 16 bytes (magic, version and reserved = 0 included). */
void encodeHeader(const Header& h, uint8_t* out16);

/** Header plus the fields validation needs. */
struct RawHeader {
    uint8_t magic;
    uint8_t version;
    uint16_t reserved;
    Header h;
};

/** Byte-wise decode of the first 16 bytes. False when len < 16. No validation. */
bool decodeHeader(const uint8_t* p, size_t len, RawHeader& out);

/** Where a downlink frame arrived. */
enum class Topic : uint8_t {
    Unicast,    // ionity/ewp1/<site>/<hwid>/dn
    Broadcast,  // ionity/ewp1/_all/dn
};

enum class Verdict : uint8_t {
    Accept,  // header is sound: continue with tool lookup / dedupe / slot
    Drop,    // not answerable (too short, not EWP): count and forget
    Reject,  // answer with ERR `status` (the rid is readable)
};

struct DownlinkCheck {
    Verdict verdict;
    uint8_t status;      // EWP_ST_* when verdict == Reject
    const char* detail;  // static text: the ERR detail, or why it was dropped
    Header h;            // valid for Accept and Reject
};

/**
 * Validation order (Phase 1 rule 7): len >= 16 -> magic -> version ->
 * unknown flag bits -> reserved == 0 -> 16 + bodyLen == len -> len <= maxFrame
 * -> type allowed on this topic (REQ/CANCEL; broadcast needs FLAG_BROADCAST)
 * -> status == OK. Tool registry, rid dedupe and slot availability follow in
 * MCPServer::onDownlink().
 */
DownlinkCheck checkDownlink(const uint8_t* p, size_t len, Topic topic, size_t maxFrame);

/** Uplink channel of a device-originated frame type (CHANNEL_POLICY in constants.ts). */
enum class Channel : uint8_t { Result, Telemetry, Event, Hello, Status, Invalid };

Channel channelOf(uint8_t msgType);

/** "rs", "tm", ... (EWP_CH_*), or nullptr for Invalid. */
const char* channelName(Channel ch);

/** QoS and retain flag of an uplink channel. */
int channelQos(Channel ch);
bool channelRetain(Channel ch);

/** Short status name for logs ("BUSY", "INVALID_ARGS", ...). Never nullptr. */
const char* statusName(uint8_t status);

/** Little-endian helpers for code that builds headers by hand (tests, tools). */
inline void storeLe16(uint8_t* p, uint16_t v) {
    p[0] = static_cast<uint8_t>(v);
    p[1] = static_cast<uint8_t>(v >> 8);
}
inline void storeLe32(uint8_t* p, uint32_t v) {
    p[0] = static_cast<uint8_t>(v);
    p[1] = static_cast<uint8_t>(v >> 8);
    p[2] = static_cast<uint8_t>(v >> 16);
    p[3] = static_cast<uint8_t>(v >> 24);
}
inline uint16_t loadLe16(const uint8_t* p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }
inline uint32_t loadLe32(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) | (static_cast<uint32_t>(p[2]) << 16) |
           (static_cast<uint32_t>(p[3]) << 24);
}

}  // namespace ewp
