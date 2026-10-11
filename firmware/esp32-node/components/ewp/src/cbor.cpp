/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | ewp/cbor.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * See cbor.h for the contract. Implementation notes:
 *  - All bounds checks are written as "needed <= remaining" with both sides
 *    already known to be in range, so no pointer is ever formed past `end_`
 *    and no size_t arithmetic can wrap.
 *  - The reader keeps `p_` unchanged on a type mismatch, so callers can try
 *    readUint() and fall back to skip() on the same item.
 */
#include "ewp/cbor.h"

#include <float.h>
#include <math.h>
#include <string.h>

namespace ewp {
namespace {

constexpr size_t kNoPos = static_cast<size_t>(-1);

constexpr uint8_t kMajorUInt = 0;
constexpr uint8_t kMajorNInt = 1;
constexpr uint8_t kMajorBytes = 2;
constexpr uint8_t kMajorText = 3;
constexpr uint8_t kMajorArray = 4;
constexpr uint8_t kMajorMap = 5;
constexpr uint8_t kMajorTag = 6;
constexpr uint8_t kMajorSimple = 7;

constexpr uint8_t kAiFalse = 20;
constexpr uint8_t kAiTrue = 21;
constexpr uint8_t kAiNull = 22;
constexpr uint8_t kAiUndefined = 23;
constexpr uint8_t kAiSimple8 = 24;
constexpr uint8_t kAiF16 = 25;
constexpr uint8_t kAiF32 = 26;
constexpr uint8_t kAiF64 = 27;

uint64_t loadBe(const uint8_t* p, size_t n) {
    uint64_t v = 0;
    for (size_t i = 0; i < n; ++i) v = (v << 8) | p[i];
    return v;
}

double halfToDouble(uint16_t h) {
    const int exp = (h >> 10) & 0x1F;
    const int mant = h & 0x3FF;
    double v;
    if (exp == 0) {
        v = ldexp(static_cast<double>(mant), -24);  // subnormal
    } else if (exp != 31) {
        v = ldexp(static_cast<double>(mant + 1024), exp - 25);
    } else {
        v = mant == 0 ? INFINITY : NAN;  // rejected by the caller
    }
    return (h & 0x8000u) ? -v : v;
}

}  // namespace

/* ------------------------------------------------------------------------- */
/* Writer                                                                     */
/* ------------------------------------------------------------------------- */

bool CborWriter::room(size_t n) {
    if (err_) return false;
    if (n > cap_ - len_) {  // len_ <= cap_ always holds, so this cannot wrap
        err_ = true;
        return false;
    }
    return true;
}

void CborWriter::head(uint8_t major, uint64_t v) {
    const uint8_t mt = static_cast<uint8_t>(major << 5);
    if (v < 24) {
        if (!room(1)) return;
        buf_[len_++] = static_cast<uint8_t>(mt | v);
    } else if (v <= 0xFFu) {
        if (!room(2)) return;
        buf_[len_++] = mt | 24;
        buf_[len_++] = static_cast<uint8_t>(v);
    } else if (v <= 0xFFFFu) {
        if (!room(3)) return;
        buf_[len_++] = mt | 25;
        buf_[len_++] = static_cast<uint8_t>(v >> 8);
        buf_[len_++] = static_cast<uint8_t>(v);
    } else if (v <= 0xFFFFFFFFu) {
        if (!room(5)) return;
        buf_[len_++] = mt | 26;
        for (int s = 24; s >= 0; s -= 8) buf_[len_++] = static_cast<uint8_t>(v >> s);
    } else {
        if (!room(9)) return;
        buf_[len_++] = mt | 27;
        for (int s = 56; s >= 0; s -= 8) buf_[len_++] = static_cast<uint8_t>(v >> s);
    }
}

void CborWriter::uint(uint64_t v) { head(kMajorUInt, v); }

void CborWriter::sint(int64_t v) {
    if (v >= 0) {
        head(kMajorUInt, static_cast<uint64_t>(v));
    } else {
        // -1 - v without overflow, also for INT64_MIN: -(v + 1) is >= 0 and <= INT64_MAX.
        head(kMajorNInt, static_cast<uint64_t>(-(v + 1)));
    }
}

void CborWriter::boolean(bool v) {
    if (!room(1)) return;
    buf_[len_++] = static_cast<uint8_t>((kMajorSimple << 5) | (v ? kAiTrue : kAiFalse));
}

void CborWriter::null() {
    if (!room(1)) return;
    buf_[len_++] = static_cast<uint8_t>((kMajorSimple << 5) | kAiNull);
}

void CborWriter::f32(float v) {
    if (!isfinite(v)) {
        err_ = true;
        return;
    }
    if (!room(5)) return;
    uint32_t bits;
    memcpy(&bits, &v, sizeof bits);  // never type-pun through a pointer cast
    buf_[len_++] = static_cast<uint8_t>((kMajorSimple << 5) | kAiF32);
    for (int s = 24; s >= 0; s -= 8) buf_[len_++] = static_cast<uint8_t>(bits >> s);
}

void CborWriter::text(const char* s, size_t n) {
    head(kMajorText, n);
    if (n == 0 || !room(n)) return;
    memcpy(buf_ + len_, s, n);
    len_ += n;
}

void CborWriter::text(const char* s) { text(s, s ? strlen(s) : 0); }

void CborWriter::bytes(const uint8_t* p, size_t n) {
    head(kMajorBytes, n);
    if (n == 0 || !room(n)) return;
    memcpy(buf_ + len_, p, n);
    len_ += n;
}

void CborWriter::raw(const uint8_t* p, size_t n) {
    if (n == 0 || !room(n)) return;
    memcpy(buf_ + len_, p, n);
    len_ += n;
}

void CborWriter::array(uint32_t n) { head(kMajorArray, n); }
void CborWriter::map(uint32_t n) { head(kMajorMap, n); }

size_t CborWriter::beginArray() {
    if (!room(1)) return kNoPos;
    const size_t at = len_;
    buf_[len_++] = static_cast<uint8_t>(kMajorArray << 5);  // count 0 until patched
    return at;
}

size_t CborWriter::beginMap() {
    if (!room(1)) return kNoPos;
    const size_t at = len_;
    buf_[len_++] = static_cast<uint8_t>(kMajorMap << 5);
    return at;
}

void CborWriter::patchCount(size_t at, uint8_t major, uint32_t count) {
    if (at == kNoPos) return;  // begin already failed; err_ is set
    // Defensive: the placeholder must be ours. A mismatch is a programming error.
    if (at >= len_ || buf_[at] != static_cast<uint8_t>(major << 5) || count > 23) {
        err_ = true;
        return;
    }
    buf_[at] = static_cast<uint8_t>((major << 5) | count);
}

void CborWriter::endArray(size_t at, uint32_t count) { patchCount(at, kMajorArray, count); }
void CborWriter::endMap(size_t at, uint32_t count) { patchCount(at, kMajorMap, count); }

/* ------------------------------------------------------------------------- */
/* Reader                                                                     */
/* ------------------------------------------------------------------------- */

bool CborReader::decodeHead(Head& h) const {
    if (err_ || p_ >= end_) return false;
    const size_t left = static_cast<size_t>(end_ - p_);
    const uint8_t ib = p_[0];
    h.major = static_cast<uint8_t>(ib >> 5);
    h.ai = static_cast<uint8_t>(ib & 0x1F);
    if (h.ai < 24) {
        h.arg = h.ai;
        h.size = 1;
        return true;
    }
    size_t extra;
    switch (h.ai) {
        case 24: extra = 1; break;
        case 25: extra = 2; break;
        case 26: extra = 4; break;
        case 27: extra = 8; break;
        default: return false;  // 28..30 reserved, 31 = indefinite length / break: unsupported
    }
    if (extra + 1 > left) return false;  // truncated head
    h.arg = loadBe(p_ + 1, extra);
    h.size = 1 + extra;
    return true;
}

CborType CborReader::peek() const {
    if (p_ >= end_) return CborType::End;
    Head h;
    if (!decodeHead(h)) return CborType::Invalid;
    switch (h.major) {
        case kMajorUInt: return CborType::UInt;
        case kMajorNInt: return CborType::NInt;
        case kMajorBytes: return CborType::Bytes;
        case kMajorText: return CborType::Text;
        case kMajorArray: return CborType::Array;
        case kMajorMap: return CborType::Map;
        case kMajorTag: return CborType::Tag;
        default: break;
    }
    switch (h.ai) {
        case kAiFalse:
        case kAiTrue: return CborType::Bool;
        case kAiNull:
        case kAiUndefined: return CborType::Null;
        case kAiF16:
        case kAiF32:
        case kAiF64: return CborType::Float;
        default: return CborType::Invalid;
    }
}

bool CborReader::readUint(uint64_t& v) {
    Head h;
    if (!decodeHead(h)) return p_ < end_ ? fail() : false;
    if (h.major != kMajorUInt) return false;
    v = h.arg;
    p_ += h.size;
    return true;
}

bool CborReader::readInt(int64_t& v) {
    Head h;
    if (!decodeHead(h)) return p_ < end_ ? fail() : false;
    if (h.major != kMajorUInt && h.major != kMajorNInt) return false;
    if (h.arg > static_cast<uint64_t>(INT64_MAX)) return fail();  // does not fit int64
    v = h.major == kMajorUInt ? static_cast<int64_t>(h.arg) : -1 - static_cast<int64_t>(h.arg);
    p_ += h.size;
    return true;
}

bool CborReader::readNumber(double& v) {
    Head h;
    if (!decodeHead(h)) return p_ < end_ ? fail() : false;
    double d;
    if (h.major == kMajorUInt) {
        d = static_cast<double>(h.arg);
    } else if (h.major == kMajorNInt) {
        d = -1.0 - static_cast<double>(h.arg);
    } else if (h.major == kMajorSimple && h.ai == kAiF16) {
        d = halfToDouble(static_cast<uint16_t>(h.arg));
    } else if (h.major == kMajorSimple && h.ai == kAiF32) {
        const uint32_t bits = static_cast<uint32_t>(h.arg);
        float f;
        memcpy(&f, &bits, sizeof f);
        d = f;
    } else if (h.major == kMajorSimple && h.ai == kAiF64) {
        const uint64_t bits = h.arg;
        memcpy(&d, &bits, sizeof d);
    } else {
        return false;
    }
    if (!isfinite(d)) return fail();  // EWP/1: non-finite floats are rejected on both sides
    v = d;
    p_ += h.size;
    return true;
}

bool CborReader::readFloat(float& v) {
    const uint8_t* const before = p_;
    double d;
    if (!readNumber(d)) return false;
    if (d > static_cast<double>(FLT_MAX) || d < -static_cast<double>(FLT_MAX)) {
        p_ = before;
        return fail();
    }
    v = static_cast<float>(d);
    return true;
}

bool CborReader::readBool(bool& v) {
    Head h;
    if (!decodeHead(h)) return p_ < end_ ? fail() : false;
    if (h.major != kMajorSimple || (h.ai != kAiFalse && h.ai != kAiTrue)) return false;
    v = h.ai == kAiTrue;
    p_ += h.size;
    return true;
}

bool CborReader::readNull() {
    Head h;
    if (!decodeHead(h)) return p_ < end_ ? fail() : false;
    if (h.major != kMajorSimple || (h.ai != kAiNull && h.ai != kAiUndefined)) return false;
    p_ += h.size;
    return true;
}

bool CborReader::readText(const char*& s, size_t& n) {
    Head h;
    if (!decodeHead(h)) return p_ < end_ ? fail() : false;
    if (h.major != kMajorText) return false;
    const size_t left = static_cast<size_t>(end_ - p_) - h.size;
    if (h.arg > left) return fail();  // claims more bytes than the frame has
    const char* text = reinterpret_cast<const char*>(p_ + h.size);
    const size_t len = static_cast<size_t>(h.arg);
    if (!utf8_valid(text, len)) return fail();
    s = text;
    n = len;
    p_ += h.size + len;
    return true;
}

bool CborReader::readBytes(const uint8_t*& out, size_t& n) {
    Head h;
    if (!decodeHead(h)) return p_ < end_ ? fail() : false;
    if (h.major != kMajorBytes) return false;
    const size_t left = static_cast<size_t>(end_ - p_) - h.size;
    if (h.arg > left) return fail();
    out = p_ + h.size;
    n = static_cast<size_t>(h.arg);
    p_ += h.size + n;
    return true;
}

bool CborReader::readArray(uint32_t& n) {
    Head h;
    if (!decodeHead(h)) return p_ < end_ ? fail() : false;
    if (h.major != kMajorArray) return false;
    const size_t left = static_cast<size_t>(end_ - p_) - h.size;
    // Every element needs at least one byte: a larger count is a lie.
    if (h.arg > left || h.arg > 0xFFFFFFFFu) return fail();
    n = static_cast<uint32_t>(h.arg);
    p_ += h.size;
    return true;
}

bool CborReader::readMap(uint32_t& n) {
    Head h;
    if (!decodeHead(h)) return p_ < end_ ? fail() : false;
    if (h.major != kMajorMap) return false;
    const size_t left = static_cast<size_t>(end_ - p_) - h.size;
    // Every pair needs at least two bytes.
    if (h.arg > left / 2 || h.arg > 0xFFFFFFFFu) return fail();
    n = static_cast<uint32_t>(h.arg);
    p_ += h.size;
    return true;
}

bool CborReader::skip() {
    if (p_ >= end_) return fail();
    return skipItem(0);
}

bool CborReader::skipItem(int depth) {
    if (depth > kMaxDepth) return fail();
    Head h;
    if (!decodeHead(h)) return fail();
    const size_t left = static_cast<size_t>(end_ - p_) - h.size;
    switch (h.major) {
        case kMajorUInt:
        case kMajorNInt:
            p_ += h.size;
            return true;
        case kMajorBytes:
        case kMajorText:
            if (h.arg > left) return fail();
            p_ += h.size + static_cast<size_t>(h.arg);
            return true;
        case kMajorArray: {
            if (h.arg > left) return fail();
            p_ += h.size;
            for (uint64_t i = 0; i < h.arg; ++i) {
                if (!skipItem(depth + 1)) return false;
            }
            return true;
        }
        case kMajorMap: {
            if (h.arg > left / 2) return fail();
            p_ += h.size;
            for (uint64_t i = 0; i < h.arg * 2; ++i) {
                if (!skipItem(depth + 1)) return false;
            }
            return true;
        }
        case kMajorTag:
            p_ += h.size;
            return skipItem(depth + 1);
        default:  // kMajorSimple
            if (h.ai == kAiFalse || h.ai == kAiTrue || h.ai == kAiNull || h.ai == kAiUndefined ||
                h.ai == kAiF16 || h.ai == kAiF32 || h.ai == kAiF64) {
                p_ += h.size;
                return true;
            }
            return fail();  // unassigned simple values (incl. ai 24) are not EWP
    }
}

/* ------------------------------------------------------------------------- */
/* UTF-8                                                                      */
/* ------------------------------------------------------------------------- */

bool utf8_valid(const char* s, size_t n) {
    const uint8_t* p = reinterpret_cast<const uint8_t*>(s);
    size_t i = 0;
    while (i < n) {
        const uint8_t c = p[i];
        if (c < 0x80) {
            ++i;
            continue;
        }
        size_t len;
        uint32_t cp;
        if (c >= 0xC2 && c <= 0xDF) {
            len = 2;
            cp = c & 0x1Fu;
        } else if (c >= 0xE0 && c <= 0xEF) {
            len = 3;
            cp = c & 0x0Fu;
        } else if (c >= 0xF0 && c <= 0xF4) {
            len = 4;
            cp = c & 0x07u;
        } else {
            return false;  // continuation byte without a lead, or 0xC0/0xC1/0xF5+ (always overlong/invalid)
        }
        if (len > n - i) return false;
        for (size_t k = 1; k < len; ++k) {
            const uint8_t cc = p[i + k];
            if ((cc & 0xC0u) != 0x80u) return false;
            cp = (cp << 6) | (cc & 0x3Fu);
        }
        if ((len == 3 && cp < 0x800) || (len == 4 && cp < 0x10000)) return false;  // overlong
        if (cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return false;       // out of range / surrogate
        i += len;
    }
    return true;
}

}  // namespace ewp
