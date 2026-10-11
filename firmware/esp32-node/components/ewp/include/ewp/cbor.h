/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | ewp/cbor.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * A bounded CBOR (RFC 8949) subset for EWP/1 bodies.
 *
 *   CborWriter  encodes into a caller-owned buffer. It NEVER allocates and NEVER
 *               writes past `capacity`: the first write that does not fit sets a
 *               sticky error flag and every later call becomes a no-op. Check
 *               ok() once at the end instead of after every call.
 *   CborReader  decodes from a caller-owned buffer, zero-copy (text/bytes are
 *               returned as views into the input). Every length is checked
 *               against the bytes that remain BEFORE it is used, nesting is
 *               limited to kMaxDepth, and indefinite-length items, reserved
 *               additional-info values and non-finite floats are rejected.
 *
 * Supported: unsigned/negative integers, byte and text strings, arrays, maps,
 * false/true/null, float16/32/64 (decode) and float32 (encode). Tags are only
 * skipped (EWP/1 defines none). That is all EWP/1 needs - a general-purpose
 * CBOR library would add code size and attack surface for nothing.
 *
 * MEMORY-SAFETY (read before changing anything here):
 *  - Byte order: CBOR numbers are BIG-endian. The 16-byte EWP header in front
 *    of the body is LITTLE-endian (frame.h). Never mix the two helpers.
 *  - Views returned by readText()/readBytes() point INTO the input buffer and
 *    are NOT NUL-terminated. They are valid only while that buffer is (for a
 *    tool handler: until it returns - the job slot is then recycled).
 *  - This code runs on frames from the network. Treat every byte as hostile.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace ewp {

/* ------------------------------------------------------------------------- */
/* Writer                                                                     */
/* ------------------------------------------------------------------------- */

class CborWriter {
public:
    /** `buf` may be nullptr only with capacity 0 (a pure size probe that always fails). */
    CborWriter(uint8_t* buf, size_t capacity) : buf_(buf), cap_(capacity) {}

    CborWriter(const CborWriter&) = delete;
    CborWriter& operator=(const CborWriter&) = delete;

    void uint(uint64_t v);
    void sint(int64_t v);
    void boolean(bool v);
    void null();
    /** float32, big-endian. Non-finite values set the error flag (EWP/1 forbids NaN/Inf). */
    void f32(float v);
    /** UTF-8 text. The caller guarantees valid UTF-8 (use utf8_valid() on foreign input). */
    void text(const char* s, size_t n);
    /** NUL-terminated UTF-8 text. */
    void text(const char* s);
    void bytes(const uint8_t* p, size_t n);
    /** Definite-length container heads: exactly `n` items (map: n key/value PAIRS) must follow. */
    void array(uint32_t n);
    void map(uint32_t n);

    /**
     * Containers whose size is only known at the end (optional keys, filtered
     * lists): beginX() writes a 1-byte placeholder and returns its offset,
     * endX() patches the count in. Counts above 23 do not fit a 1-byte head
     * and set the error flag - use the definite form for big containers.
     */
    size_t beginArray();
    void endArray(size_t at, uint32_t count);
    size_t beginMap();
    void endMap(size_t at, uint32_t count);

    /** Copy pre-encoded CBOR (already validated by the caller) verbatim. */
    void raw(const uint8_t* p, size_t n);

    bool ok() const { return !err_; }
    size_t size() const { return len_; }
    size_t capacity() const { return cap_; }
    size_t remaining() const { return cap_ - len_; }
    const uint8_t* data() const { return buf_; }
    /** Mark the output unusable (e.g. a handler found its own result inconsistent). */
    void fail() { err_ = true; }

private:
    void head(uint8_t major, uint64_t v);
    bool room(size_t n);
    void patchCount(size_t at, uint8_t major, uint32_t count);

    uint8_t* buf_;
    size_t cap_;
    size_t len_ = 0;
    bool err_ = false;
};

/** RAII helper for a map with optional keys: count is tallied by key() and patched on scope exit. */
class CborMap {
public:
    explicit CborMap(CborWriter& w) : w_(w), at_(w.beginMap()) {}
    ~CborMap() { w_.endMap(at_, n_); }
    CborMap(const CborMap&) = delete;
    CborMap& operator=(const CborMap&) = delete;
    /** Writes the key; the caller writes exactly one value right after. */
    CborWriter& key(uint32_t k) {
        ++n_;
        w_.uint(k);
        return w_;
    }

private:
    CborWriter& w_;
    size_t at_;
    uint32_t n_ = 0;
};

/** RAII helper for a small array (<= 23 items) whose length is only known at the end. */
class CborArray {
public:
    explicit CborArray(CborWriter& w) : w_(w), at_(w.beginArray()) {}
    ~CborArray() { w_.endArray(at_, n_); }
    CborArray(const CborArray&) = delete;
    CborArray& operator=(const CborArray&) = delete;
    /** Call once per element, before writing it. */
    CborWriter& next() {
        ++n_;
        return w_;
    }
    uint32_t count() const { return n_; }

private:
    CborWriter& w_;
    size_t at_;
    uint32_t n_ = 0;
};

/* ------------------------------------------------------------------------- */
/* Reader                                                                     */
/* ------------------------------------------------------------------------- */

enum class CborType : uint8_t {
    UInt,    // major 0
    NInt,    // major 1
    Bytes,   // major 2
    Text,    // major 3
    Array,   // major 4
    Map,     // major 5
    Tag,     // major 6
    Bool,    // major 7: false / true
    Null,    // major 7: null / undefined
    Float,   // major 7: float16 / 32 / 64
    End,     // no bytes left
    Invalid, // reserved / unsupported encoding at the cursor
};

class CborReader {
public:
    /** Nesting limit for skip(): EWP/1 bodies are at most 4 levels deep. */
    static constexpr int kMaxDepth = 8;

    CborReader(const uint8_t* p, size_t n) : p_(p), end_(p + n) {}

    CborType peek() const;
    bool atEnd() const { return p_ == end_; }
    size_t remaining() const { return static_cast<size_t>(end_ - p_); }
    bool ok() const { return !err_; }
    /** Byte offset of the cursor from the start of the input (for diagnostics). */
    const uint8_t* cursor() const { return p_; }

    /*
     * Typed reads. Each returns false WITHOUT moving the cursor when the next
     * item has a different type, and false WITH the sticky error flag set when
     * the item is malformed (truncated, reserved encoding, bad UTF-8, ...).
     */
    bool readUint(uint64_t& v);
    bool readInt(int64_t& v);
    /** Integer or float16/32/64 ("float fields may arrive as CBOR ints"); finite only. */
    bool readNumber(double& v);
    /** readNumber() narrowed to float32; values outside float range are rejected. */
    bool readFloat(float& v);
    bool readBool(bool& v);
    bool readNull();
    /** Text view into the input (not NUL-terminated); validated as UTF-8. */
    bool readText(const char*& s, size_t& n);
    bool readBytes(const uint8_t*& p, size_t& n);
    /** Definite-length heads only. The count is sanity-checked against the bytes left. */
    bool readArray(uint32_t& n);
    bool readMap(uint32_t& n);

    /** Skip exactly one complete data item (any type, nested up to kMaxDepth). */
    bool skip();

private:
    struct Head {
        uint8_t major;
        uint8_t ai;      // additional information (low 5 bits)
        uint64_t arg;    // argument value (length, integer, or raw float bits)
        size_t size;     // bytes the head occupies
    };
    bool decodeHead(Head& h) const;
    bool skipItem(int depth);
    bool fail() {
        err_ = true;
        return false;
    }

    const uint8_t* p_;
    const uint8_t* end_;
    bool err_ = false;
};

/** Strict UTF-8 validation (no overlongs, no surrogates, max U+10FFFF). */
bool utf8_valid(const char* s, size_t n);

}  // namespace ewp
