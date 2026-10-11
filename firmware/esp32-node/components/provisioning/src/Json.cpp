/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/Json.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "prov/Json.h"

#include <stdio.h>
#include <string.h>

namespace prov {
namespace {

/** Length of the valid UTF-8 sequence at p (1..4), or 0 when it is not valid. */
size_t utf8SeqLen(const uint8_t* p, size_t left) {
    const uint8_t c = p[0];
    size_t len;
    uint32_t cp;
    if (c < 0x80) return 1;
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
        return 0;
    }
    if (len > left) return 0;
    for (size_t k = 1; k < len; ++k) {
        if ((p[k] & 0xC0u) != 0x80u) return 0;
        cp = (cp << 6) | (p[k] & 0x3Fu);
    }
    if ((len == 3 && cp < 0x800) || (len == 4 && cp < 0x10000) || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
        return 0;
    }
    return len;
}

}  // namespace

void JsonWriter::reset(char* buf, size_t cap) {
    buf_ = buf;
    cap_ = cap > 0 ? cap - 1 : 0;
    len_ = 0;
    err_ = buf == nullptr || cap == 0;
    depth_ = 0;
    afterKey_ = false;
    if (!err_) buf_[0] = '\0';
}

void JsonWriter::raw(const char* s, size_t n) {
    if (err_) return;
    if (n > cap_ - len_) {
        err_ = true;
        return;
    }
    memcpy(buf_ + len_, s, n);
    len_ += n;
    buf_[len_] = '\0';
}

void JsonWriter::put(char c) { raw(&c, 1); }

void JsonWriter::separator() {
    if (afterKey_) {
        afterKey_ = false;
        return;
    }
    if (depth_ > 0) {
        if (!first_[depth_ - 1]) put(',');
        first_[depth_ - 1] = false;
    }
}

void JsonWriter::beginObject() {
    separator();
    if (depth_ >= kMaxDepth) {
        err_ = true;
        return;
    }
    put('{');
    first_[depth_++] = true;
}

void JsonWriter::endObject() {
    if (depth_ == 0) {
        err_ = true;
        return;
    }
    --depth_;
    put('}');
}

void JsonWriter::beginArray() {
    separator();
    if (depth_ >= kMaxDepth) {
        err_ = true;
        return;
    }
    put('[');
    first_[depth_++] = true;
}

void JsonWriter::endArray() {
    if (depth_ == 0) {
        err_ = true;
        return;
    }
    --depth_;
    put(']');
}

void JsonWriter::key(const char* k) {
    separator();
    quoted(k, k ? strlen(k) : 0);
    put(':');
    afterKey_ = true;
}

void JsonWriter::str(const char* s) { str(s, s ? strlen(s) : 0); }

void JsonWriter::str(const char* s, size_t n) {
    separator();
    quoted(s, n);
}

void JsonWriter::quoted(const char* s, size_t n) {
    put('"');
    const uint8_t* p = reinterpret_cast<const uint8_t*>(s);
    size_t i = 0;
    while (i < n && !err_) {
        const uint8_t c = p[i];
        if (c == '"' || c == '\\') {
            const char esc[2] = {'\\', static_cast<char>(c)};
            raw(esc, 2);
            ++i;
        } else if (c < 0x20 || c == 0x7F) {
            char esc[7];
            snprintf(esc, sizeof esc, "\\u%04x", c);
            raw(esc, 6);
            ++i;
        } else if (c < 0x80) {
            put(static_cast<char>(c));
            ++i;
        } else {
            const size_t len = utf8SeqLen(p + i, n - i);
            if (len == 0) {
                raw("\\ufffd", 6);  // not UTF-8: replacement character, never raw bytes
                ++i;
            } else {
                raw(reinterpret_cast<const char*>(p + i), len);
                i += len;
            }
        }
    }
    put('"');
}

void JsonWriter::num(int64_t v) {
    separator();
    char b[24];
    const int n = snprintf(b, sizeof b, "%lld", static_cast<long long>(v));
    raw(b, static_cast<size_t>(n));
}

void JsonWriter::boolean(bool v) {
    separator();
    if (v) raw("true", 4);
    else raw("false", 5);
}

void JsonWriter::null() {
    separator();
    raw("null", 4);
}

bool jsonShapeOk(const char* s, size_t n, int maxValues, int maxDepth) {
    int values = 1;
    int depth = 0;
    bool inStr = false;
    for (size_t i = 0; i < n; ++i) {
        const char c = s[i];
        if (inStr) {
            if (c == '\\') {
                ++i;  // skip the escaped character (for \uXXXX the hex digits are harmless)
            } else if (c == '"') {
                inStr = false;
            }
            continue;
        }
        switch (c) {
            case '"': inStr = true; break;
            case '{':
            case '[':
                if (++depth > maxDepth) return false;
                if (++values > maxValues) return false;
                break;
            case '}':
            case ']':
                if (--depth < 0) return false;
                break;
            case ',':
                if (++values > maxValues) return false;
                break;
            default: break;
        }
    }
    return true;
}

}  // namespace prov
