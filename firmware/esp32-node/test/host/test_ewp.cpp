/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/test_ewp.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Unit tests: CRC-32, CBOR writer/reader, frame header and downlink checks.
 */
#include <math.h>
#include <stdint.h>
#include <string.h>

#include "check.h"
#include "ewp/cbor.h"
#include "ewp/crc32.h"
#include "ewp/frame.h"

using namespace ewp;

/* ---------------------------------------------------------------- CRC-32 */

TEST(crc32_check_value) {
    CHECK_EQ(crc32("123456789", 9), 0xCBF43926u);
    CHECK_EQ(crc32(nullptr, 0), 0u);
    char hex[9];
    crc32_hex(0xCBF43926u, hex);
    CHECK_STR(hex, "cbf43926");
    crc32_hex(0x0000000Au, hex);
    CHECK_STR(hex, "0000000a");
}

TEST(crc32_incremental_equals_one_shot) {
    const char* s = "{\"ionity\":\"prov\",\"op\":\"get\",\"v\":2,\"id\":7}";
    const size_t n = strlen(s);
    for (size_t cut = 0; cut <= n; ++cut) {
        CHECK_EQ(crc32_update(crc32(s, cut), s + cut, n - cut), crc32(s, n));
    }
}

TEST(crc32_parse_hex_is_strict) {
    uint32_t v = 0;
    CHECK(crc32_parse_hex("cbf43926", &v));
    CHECK_EQ(v, 0xCBF43926u);
    CHECK(!crc32_parse_hex("CBF43926", &v));  // uppercase = corruption on this wire
    CHECK(!crc32_parse_hex("cbf4392g", &v));
    CHECK(!crc32_parse_hex("cbf4392", &v));  // NUL inside the 8 digits
}

/* ---------------------------------------------------------------- CBOR writer */

TEST(cbor_uint_shortest_form) {
    uint8_t b[16];
    struct {
        uint64_t v;
        const char* hex;
    } cases[] = {
        {0, "00"},
        {23, "17"},
        {24, "18 18"},
        {255, "18 ff"},
        {256, "19 01 00"},
        {65535, "19 ff ff"},
        {65536, "1a 00 01 00 00"},
        {0xFFFFFFFFull, "1a ff ff ff ff"},
        {0x100000000ull, "1b 00 00 00 01 00 00 00 00"},
    };
    for (const auto& c : cases) {
        CborWriter w(b, sizeof b);
        w.uint(c.v);
        CHECK(w.ok());
        CHECK_HEX(b, w.size(), c.hex);
    }
}

TEST(cbor_sint_and_extremes) {
    uint8_t b[16];
    {
        CborWriter w(b, sizeof b);
        w.sint(-1);
        w.sint(-24);
        w.sint(-25);
        w.sint(500);
        CHECK(w.ok());
        CHECK_HEX(b, w.size(), "20 37 38 18 19 01 f4");
    }
    {
        CborWriter w(b, sizeof b);
        w.sint(INT64_MIN);
        CHECK(w.ok());
        CHECK_HEX(b, w.size(), "3b 7f ff ff ff ff ff ff ff");
        CborReader r(b, w.size());
        int64_t v = 0;
        CHECK(r.readInt(v));
        CHECK(v == INT64_MIN);
    }
}

TEST(cbor_text_bytes_bool_null_float) {
    uint8_t b[64];
    CborWriter w(b, sizeof b);
    w.text("ok");
    const uint8_t raw[] = {0xde, 0xad};
    w.bytes(raw, 2);
    w.boolean(true);
    w.boolean(false);
    w.null();
    w.f32(1.5f);
    CHECK(w.ok());
    CHECK_HEX(b, w.size(), "62 6f 6b 42 de ad f5 f4 f6 fa 3f c0 00 00");
}

TEST(cbor_writer_rejects_non_finite_float) {
    uint8_t b[16];
    CborWriter w(b, sizeof b);
    w.f32(NAN);
    CHECK(!w.ok());
    CborWriter w2(b, sizeof b);
    w2.f32(INFINITY);
    CHECK(!w2.ok());
}

TEST(cbor_writer_never_writes_past_capacity) {
    uint8_t b[8 + 4];
    memset(b, 0xCC, sizeof b);  // canary after the 8 usable bytes
    CborWriter w(b, 8);
    w.text("0123456789");  // 11 bytes: does not fit
    CHECK(!w.ok());
    w.uint(1);  // sticky: no-op
    CHECK(!w.ok());
    for (size_t i = 8; i < sizeof b; ++i) CHECK_EQ(b[i], 0xCC);
    CHECK(w.size() <= 8);
}

TEST(cbor_map_and_array_patching) {
    uint8_t b[64];
    CborWriter w(b, sizeof b);
    {
        CborMap m(w);
        m.key(1).uint(7);
        m.key(3).text("x");
        {
            CborArray a(m.key(9));
            a.next().uint(1);
            a.next().uint(2);
        }
    }
    CHECK(w.ok());
    CHECK_HEX(b, w.size(), "a3 01 07 03 61 78 09 82 01 02");
}

TEST(cbor_patch_over_23_is_an_error) {
    uint8_t b[128];
    CborWriter w(b, sizeof b);
    const size_t at = w.beginArray();
    for (int i = 0; i < 24; ++i) w.uint(0);
    w.endArray(at, 24);
    CHECK(!w.ok());
}

TEST(cbor_patch_after_failed_begin_is_harmless) {
    uint8_t b[1];
    CborWriter w(b, 0);
    const size_t at = w.beginMap();
    w.endMap(at, 1);
    CHECK(!w.ok());
}

/* ---------------------------------------------------------------- CBOR reader */

TEST(cbor_reader_roundtrip) {
    uint8_t b[128];
    CborWriter w(b, sizeof b);
    w.map(4);
    w.uint(1);
    w.uint(300);
    w.uint(2);
    w.sint(-7);
    w.uint(3);
    w.text("héllo");
    w.uint(4);
    w.f32(0.25f);
    CHECK(w.ok());

    CborReader r(b, w.size());
    uint32_t n = 0;
    CHECK(r.readMap(n));
    CHECK_EQ(n, 4u);
    uint64_t k = 0, u = 0;
    int64_t s = 0;
    const char* t = nullptr;
    size_t tl = 0;
    double d = 0;
    CHECK(r.readUint(k) && k == 1 && r.readUint(u) && u == 300);
    CHECK(r.readUint(k) && k == 2 && r.readInt(s) && s == -7);
    CHECK(r.readUint(k) && k == 3 && r.readText(t, tl) && tl == 6 && memcmp(t, "héllo", 6) == 0);
    CHECK(r.readUint(k) && k == 4 && r.readNumber(d) && d == 0.25);
    CHECK(r.atEnd());
    CHECK(r.ok());
}

TEST(cbor_reader_type_mismatch_keeps_cursor) {
    const uint8_t b[] = {0x62, 'o', 'k'};
    CborReader r(b, sizeof b);
    uint64_t v;
    CHECK(!r.readUint(v));
    CHECK(r.ok());  // a mismatch is not an error
    const char* t;
    size_t n;
    CHECK(r.readText(t, n) && n == 2);
}

TEST(cbor_reader_accepts_ints_as_floats) {
    const uint8_t b[] = {0x01, 0x20, 0xf9, 0x3c, 0x00, 0xfb, 0x3f, 0xd0, 0, 0, 0, 0, 0, 0};
    CborReader r(b, sizeof b);
    float f = 0;
    CHECK(r.readFloat(f) && f == 1.0f);   // uint 1
    CHECK(r.readFloat(f) && f == -1.0f);  // nint -1
    CHECK(r.readFloat(f) && f == 1.0f);   // float16 1.0
    CHECK(r.readFloat(f) && f == 0.25f);  // float64 0.25
    CHECK(r.atEnd());
}

TEST(cbor_reader_half_float_subnormal) {
    const uint8_t b[] = {0xf9, 0x00, 0x01};
    CborReader r(b, sizeof b);
    double d = 0;
    CHECK(r.readNumber(d));
    CHECK(fabs(d - 5.960464477539063e-08) < 1e-20);
}

TEST(cbor_reader_rejects_non_finite) {
    const uint8_t inf16[] = {0xf9, 0x7c, 0x00};
    const uint8_t nan32[] = {0xfa, 0x7f, 0xc0, 0x00, 0x00};
    const uint8_t big64[] = {0xfb, 0x7f, 0xef, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff};  // DBL_MAX
    double d;
    float f;
    {
        CborReader r(inf16, sizeof inf16);
        CHECK(!r.readNumber(d));
        CHECK(!r.ok());
    }
    {
        CborReader r(nan32, sizeof nan32);
        CHECK(!r.readNumber(d));
        CHECK(!r.ok());
    }
    {
        CborReader r(big64, sizeof big64);
        CHECK(!r.readFloat(f));  // finite double, but not a float32
        CHECK(!r.ok());
    }
}

TEST(cbor_reader_rejects_lying_lengths) {
    const uint8_t text[] = {0x65, 'a', 'b'};          // claims 5 bytes, has 2
    const uint8_t arr[] = {0x9a, 0x7f, 0xff, 0xff, 0xff};  // 2^31-1 elements, 0 bytes
    const uint8_t map[] = {0xa2, 0x01, 0x02};         // 2 pairs need 4 bytes, 2 left
    const char* t;
    size_t n;
    uint32_t c;
    {
        CborReader r(text, sizeof text);
        CHECK(!r.readText(t, n));
        CHECK(!r.ok());
    }
    {
        CborReader r(arr, sizeof arr);
        CHECK(!r.readArray(c));
        CHECK(!r.ok());
    }
    {
        CborReader r(map, sizeof map);
        CHECK(!r.readMap(c));
        CHECK(!r.ok());
    }
}

TEST(cbor_reader_rejects_indefinite_and_reserved) {
    const uint8_t indef[] = {0x9f, 0x01, 0xff};
    const uint8_t reserved[] = {0x1c};
    const uint8_t simple8[] = {0xf8, 0x20};
    {
        CborReader r(indef, sizeof indef);
        CHECK(r.peek() == CborType::Invalid);
        CHECK(!r.skip());
    }
    {
        CborReader r(reserved, sizeof reserved);
        uint64_t v;
        CHECK(!r.readUint(v));
        CHECK(!r.ok());
    }
    {
        CborReader r(simple8, sizeof simple8);
        CHECK(!r.skip());
    }
}

TEST(cbor_reader_truncated_heads) {
    const uint8_t b[] = {0x19, 0x01};  // uint16 with 1 byte
    CborReader r(b, sizeof b);
    uint64_t v;
    CHECK(!r.readUint(v));
    CHECK(!r.ok());
}

TEST(cbor_skip_depth_limit) {
    uint8_t deep[32];
    size_t n = 0;
    for (int i = 0; i < 12; ++i) deep[n++] = 0x81;  // [[[[...
    deep[n++] = 0x00;
    CborReader r(deep, n);
    CHECK(!r.skip());  // 12 levels > kMaxDepth

    uint8_t ok[16];
    size_t m = 0;
    for (int i = 0; i < 8; ++i) ok[m++] = 0x81;
    ok[m++] = 0x00;
    CborReader r2(ok, m);
    CHECK(r2.skip());
    CHECK(r2.atEnd());
}

TEST(cbor_skip_tags_and_nested_maps) {
    // tag 1 (epoch) 1000, then {1: [1, "a"], 2: {3: h'00'}}
    const uint8_t b[] = {0xc1, 0x19, 0x03, 0xe8, 0xa2, 0x01, 0x82, 0x01, 0x61, 0x61, 0x02, 0xa1, 0x03, 0x41, 0x00};
    CborReader r(b, sizeof b);
    CHECK(r.skip());
    CHECK(r.skip());
    CHECK(r.atEnd());
    CHECK(r.ok());
}

TEST(cbor_text_must_be_utf8) {
    const uint8_t bad[] = {0x62, 0xc3, 0x28};  // invalid 2-byte sequence
    CborReader r(bad, sizeof bad);
    const char* t;
    size_t n;
    CHECK(!r.readText(t, n));
    CHECK(!r.ok());
}

TEST(utf8_validation_cases) {
    CHECK(utf8_valid("plain", 5));
    CHECK(utf8_valid("\xc3\xa9", 2));              // é
    CHECK(utf8_valid("\xe2\x82\xac", 3));          // €
    CHECK(utf8_valid("\xf0\x9f\x98\x80", 4));      // 😀
    CHECK(!utf8_valid("\xc0\xaf", 2));             // overlong '/'
    CHECK(!utf8_valid("\xe0\x80\xaf", 3));         // overlong
    CHECK(!utf8_valid("\xed\xa0\x80", 3));         // surrogate D800
    CHECK(!utf8_valid("\xf4\x90\x80\x80", 4));     // > U+10FFFF
    CHECK(!utf8_valid("\x80", 1));                 // lone continuation
    CHECK(!utf8_valid("\xe2\x82", 2));             // truncated
}

/* ---------------------------------------------------------------- frame */

// Phase 1 §4.5 reference request: REQ, ACK_REQUESTED, rid 0x0100002A, tool 3,
// deadline 3000 ms, body {1: 3, 2: {1: 0, 2: 1000}, 3: 3}.
static const char* kRefRequest =
    "e5 01 01 01 2a 00 00 01 03 00 b8 0b 0d 00 00 00 a3 01 03 02 a2 01 00 02 19 03 e8 03 03";

TEST(frame_reference_request_accepts) {
    uint8_t f[64];
    const size_t n = hexToBytes(kRefRequest, f, sizeof f);
    CHECK_EQ(n, 29u);
    const DownlinkCheck c = checkDownlink(f, n, Topic::Unicast, EWP_LIMIT_DOWNLINK_FRAME);
    CHECK(c.verdict == Verdict::Accept);
    CHECK_EQ(c.h.type, EWP_MSG_REQ);
    CHECK_EQ(c.h.flags, EWP_FLAG_ACK_REQUESTED);
    CHECK_EQ(c.h.rid, 0x0100002Au);
    CHECK_EQ(c.h.tool, 3);
    CHECK_EQ(c.h.deadlineMs, 3000);
    CHECK_EQ(c.h.bodyLen, 13);

    CborReader r(f + 16, c.h.bodyLen);
    uint32_t pairs = 0;
    CHECK(r.readMap(pairs) && pairs == 3);
    uint64_t k, v;
    CHECK(r.readUint(k) && k == 1 && r.readUint(v) && v == 3);
    CHECK(r.readUint(k) && k == 2);
    uint32_t inner = 0;
    CHECK(r.readMap(inner) && inner == 2);
    CHECK(r.readUint(k) && k == 1 && r.readUint(v) && v == 0);
    CHECK(r.readUint(k) && k == 2 && r.readUint(v) && v == 1000);
    CHECK(r.readUint(k) && k == 3 && r.readUint(v) && v == 3);
    CHECK(r.atEnd());
}

TEST(frame_header_roundtrip_is_little_endian) {
    Header h;
    h.type = EWP_MSG_RES;
    h.flags = EWP_FLAG_BUFFERED;
    h.rid = 0x11223344u;
    h.tool = EWP_TOOL_READ_TELEMETRY;
    h.status = EWP_ST_OK;
    h.deadlineMs = 0x0102;
    h.bodyLen = 0x0304;
    uint8_t b[16];
    encodeHeader(h, b);
    CHECK_HEX(b, 16, "e5 01 03 08 44 33 22 11 02 00 02 01 04 03 00 00");
    RawHeader raw;
    CHECK(decodeHeader(b, 16, raw));
    CHECK_EQ(raw.h.rid, 0x11223344u);
    CHECK_EQ(raw.h.deadlineMs, 0x0102);
    CHECK_EQ(raw.h.bodyLen, 0x0304);
    CHECK_EQ(raw.reserved, 0);
}

TEST(frame_header_from_unaligned_buffer) {
    // The transport buffer has no alignment guarantee: decode from an odd address.
    uint8_t storage[40];
    uint8_t* odd = storage + 1;
    const size_t n = hexToBytes(kRefRequest, odd, sizeof storage - 1);
    RawHeader raw;
    CHECK(decodeHeader(odd, n, raw));
    CHECK_EQ(raw.h.rid, 0x0100002Au);
}

static DownlinkCheck checkHex(const char* hex, Topic t = Topic::Unicast, size_t max = EWP_LIMIT_DOWNLINK_FRAME) {
    static uint8_t f[128];
    const size_t n = hexToBytes(hex, f, sizeof f);
    return checkDownlink(f, n, t, max);
}

TEST(frame_downlink_validation_order) {
    // 1. too short -> drop
    CHECK(checkHex("e5 01 01").verdict == Verdict::Drop);
    // 2. legacy JSON on the topic -> drop
    CHECK(checkHex("7b 22 6f 70 22 3a 31 7d 00 00 00 00 00 00 00 00").verdict == Verdict::Drop);
    // 3. version 2 -> UNSUPPORTED_VERSION with the rid
    DownlinkCheck c = checkHex("e5 02 01 00 2a 00 00 01 02 00 00 00 00 00 00 00");
    CHECK(c.verdict == Verdict::Reject);
    CHECK_EQ(c.status, EWP_ST_UNSUPPORTED_VERSION);
    CHECK_EQ(c.h.rid, 0x0100002Au);
    // 4. unknown flag bit 0x20
    c = checkHex("e5 01 01 20 2a 00 00 01 02 00 00 00 00 00 00 00");
    CHECK(c.verdict == Verdict::Reject && c.status == EWP_ST_BAD_FRAME);
    // 5. reserved != 0
    c = checkHex("e5 01 01 00 2a 00 00 01 02 00 00 00 00 00 01 00");
    CHECK(c.verdict == Verdict::Reject && c.status == EWP_ST_BAD_FRAME);
    // 6. bodyLen 2 but 1 byte follows
    c = checkHex("e5 01 01 00 2a 00 00 01 02 00 00 00 02 00 00 00 a0");
    CHECK(c.verdict == Verdict::Reject && c.status == EWP_ST_BAD_FRAME);
    // 7. bigger than this build's limit (limit 16 here)
    c = checkHex("e5 01 01 00 2a 00 00 01 02 00 00 00 01 00 00 00 a0", Topic::Unicast, 16);
    CHECK(c.verdict == Verdict::Reject && c.status == EWP_ST_TOO_LARGE);
    // 8. device frame (RES) on the downlink
    c = checkHex("e5 01 03 00 2a 00 00 01 02 00 00 00 01 00 00 00 a0");
    CHECK(c.verdict == Verdict::Reject && c.status == EWP_ST_BAD_FRAME);
    // 8b. broadcast topic without FLAG_BROADCAST
    c = checkHex("e5 01 01 00 2a 00 00 01 02 00 00 00 01 00 00 00 a0", Topic::Broadcast);
    CHECK(c.verdict == Verdict::Reject && c.status == EWP_ST_BAD_FRAME);
    // ... with the flag it is fine
    c = checkHex("e5 01 01 02 2a 00 00 01 02 00 00 00 01 00 00 00 a0", Topic::Broadcast);
    CHECK(c.verdict == Verdict::Accept);
    // 9. REQ with a non-OK status
    c = checkHex("e5 01 01 00 2a 00 00 01 02 30 00 00 01 00 00 00 a0");
    CHECK(c.verdict == Verdict::Reject && c.status == EWP_ST_BAD_FRAME);
    // CANCEL is allowed on dn
    c = checkHex("e5 01 05 00 2a 00 00 01 00 00 00 00 03 00 00 00 a1 01 01");
    CHECK(c.verdict == Verdict::Accept && c.h.type == EWP_MSG_CANCEL);
}

TEST(frame_channel_policy) {
    CHECK(channelOf(EWP_MSG_ACK) == Channel::Result);
    CHECK(channelOf(EWP_MSG_ERR) == Channel::Result);
    CHECK(channelOf(EWP_MSG_TELEMETRY) == Channel::Telemetry);
    CHECK(channelOf(EWP_MSG_LOG) == Channel::Event);
    CHECK(channelOf(EWP_MSG_HELLO) == Channel::Hello);
    CHECK(channelOf(EWP_MSG_STATUS) == Channel::Status);
    CHECK(channelOf(EWP_MSG_REQ) == Channel::Invalid);
    CHECK_STR(channelName(Channel::Result), "rs");
    CHECK_STR(channelName(Channel::Hello), "hello");
    CHECK_EQ(channelQos(Channel::Telemetry), 0);
    CHECK_EQ(channelQos(Channel::Status), 1);
    CHECK(channelRetain(Channel::Hello));
    CHECK(channelRetain(Channel::Status));
    CHECK(!channelRetain(Channel::Result));
    CHECK_STR(statusName(EWP_ST_BUSY), "BUSY");
    CHECK_STR(statusName(0xEE), "UNKNOWN_STATUS");
}
