/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/frames.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Test helpers: build downlink frames, drain the uplink, look into CBOR maps.
 */
#pragma once

#include <stdint.h>
#include <string.h>

#include <string>
#include <vector>

#include "ewp/cbor.h"
#include "ewp/frame.h"
#include "mcp/Uplink.h"
#include "mcp/os.h"

namespace tf {

using Bytes = std::vector<uint8_t>;

/** A downlink frame: header + raw CBOR body. */
inline Bytes frame(uint8_t type, uint32_t rid, uint8_t tool, uint8_t flags = 0, uint16_t deadlineMs = 0,
                   const Bytes& body = {0xA0}) {
    Bytes f(EWP_HEADER_LEN + body.size());
    ewp::Header h;
    h.type = type;
    h.flags = flags;
    h.rid = rid;
    h.tool = tool;
    h.deadlineMs = deadlineMs;
    h.bodyLen = static_cast<uint16_t>(body.size());
    ewp::encodeHeader(h, f.data());
    if (!body.empty()) memcpy(f.data() + EWP_HEADER_LEN, body.data(), body.size());
    return f;
}

inline Bytes req(uint32_t rid, uint8_t tool, uint8_t flags = 0, uint16_t deadlineMs = 0, const Bytes& body = {0xA0}) {
    return frame(EWP_MSG_REQ, rid, tool, flags, deadlineMs, body);
}

inline Bytes cancel(uint32_t rid, uint8_t flags = 0) { return frame(EWP_MSG_CANCEL, rid, 0, flags, 0, {0xA1, 0x01, 0x01}); }

/** One uplink frame copied out of the pool. */
struct Out {
    ewp::Header h;
    Bytes bytes;
    uint8_t channel = 0;
    const uint8_t* body() const { return bytes.data() + EWP_HEADER_LEN; }
    size_t bodyLen() const { return bytes.size() - EWP_HEADER_LEN; }
};

/** Next due frame within timeoutMs (copied; the slot is released). */
inline bool pop(mcp::Uplink& up, Out& out, uint32_t timeoutMs = 1000) {
    const int64_t until = os::nowUs() + static_cast<int64_t>(timeoutMs) * 1000;
    mcp::TxItem it;
    while (os::nowUs() < until) {
        if (up.nextDue(it, 20)) {
            const uint8_t* p = up.buffer(it.slot);
            out.bytes.assign(p, p + it.len);
            out.channel = it.channel;
            ewp::RawHeader raw;
            ewp::decodeHeader(p, it.len, raw);
            out.h = raw.h;
            up.release(it.slot);
            return true;
        }
    }
    return false;
}

/** Drain everything that is due within timeoutMs. */
inline std::vector<Out> drain(mcp::Uplink& up, uint32_t timeoutMs = 200) {
    std::vector<Out> v;
    Out o;
    while (pop(up, o, timeoutMs)) v.push_back(o);
    return v;
}

/* ---- CBOR map lookups (top-level map, unsigned keys) -------------------- */

/** Position `r` on the value of `key` in the map that `r` currently points at. */
inline bool seek(ewp::CborReader& r, uint64_t key) {
    uint32_t n;
    if (!r.readMap(n)) return false;
    for (uint32_t i = 0; i < n; ++i) {
        uint64_t k;
        if (!r.readUint(k)) return false;
        if (k == key) return true;
        if (!r.skip()) return false;
    }
    return false;
}

inline bool getUint(const uint8_t* p, size_t n, uint64_t key, uint64_t& v) {
    ewp::CborReader r(p, n);
    return seek(r, key) && r.readUint(v);
}

inline bool getInt(const uint8_t* p, size_t n, uint64_t key, int64_t& v) {
    ewp::CborReader r(p, n);
    return seek(r, key) && r.readInt(v);
}

inline bool getText(const uint8_t* p, size_t n, uint64_t key, std::string& v) {
    ewp::CborReader r(p, n);
    const char* s;
    size_t len;
    if (!seek(r, key) || !r.readText(s, len)) return false;
    v.assign(s, len);
    return true;
}

/** Byte range of the value of `key` (any type). */
inline bool getRaw(const uint8_t* p, size_t n, uint64_t key, const uint8_t*& vp, size_t& vn) {
    ewp::CborReader r(p, n);
    if (!seek(r, key)) return false;
    const uint8_t* start = r.cursor();
    if (!r.skip()) return false;
    vp = start;
    vn = static_cast<size_t>(r.cursor() - start);
    return true;
}

inline bool hasKey(const uint8_t* p, size_t n, uint64_t key) {
    ewp::CborReader r(p, n);
    return seek(r, key);
}

/** Whole buffer is exactly one well-formed CBOR item. */
inline bool wellFormed(const uint8_t* p, size_t n) {
    ewp::CborReader r(p, n);
    return r.skip() && r.atEnd();
}

}  // namespace tf
