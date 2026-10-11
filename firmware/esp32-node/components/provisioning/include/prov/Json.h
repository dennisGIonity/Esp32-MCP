/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/Json.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * JSON for the ionity-prov/2 serial protocol.
 *
 *   JsonWriter   builds a reply into a fixed buffer (no heap, no printf of
 *                user strings). Strings are escaped; bytes that are not valid
 *                UTF-8 (a Wi-Fi SSID can be any 32 bytes) become U+FFFD, so
 *                the host always receives valid JSON.
 *   jsonShapeOk  bounds a request BEFORE cJSON sees it: cJSON allocates one
 *                node per value, so a 4 KiB line of "[1,1,1,..." would cost
 *                ~80 KiB of heap. A real request has < 30 values.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace prov {

class JsonWriter {
public:
    JsonWriter(char* buf, size_t cap) { reset(buf, cap); }

    /** Start over on a (new) buffer. */
    void reset(char* buf, size_t cap);

    JsonWriter(const JsonWriter&) = delete;
    JsonWriter& operator=(const JsonWriter&) = delete;

    void beginObject();
    void endObject();
    void beginArray();
    void endArray();
    /** Object key; the next call writes its value. */
    void key(const char* k);

    void str(const char* s);
    void str(const char* s, size_t n);
    void num(int64_t v);
    void boolean(bool v);
    void null();

    /* key + value shorthands */
    void kv(const char* k, const char* v) {
        key(k);
        str(v);
    }
    void kvNum(const char* k, int64_t v) {
        key(k);
        num(v);
    }
    void kvBool(const char* k, bool v) {
        key(k);
        boolean(v);
    }

    bool ok() const { return !err_; }
    size_t size() const { return len_; }
    /** NUL-terminated text (valid while the writer's buffer is). */
    const char* c_str() const { return buf_; }

private:
    void raw(const char* s, size_t n);
    void put(char c);
    /** "..." with JSON escaping; no separator handling. */
    void quoted(const char* s, size_t n);
    void separator();

    static constexpr int kMaxDepth = 8;
    char* buf_;
    size_t cap_;  // usable bytes (one is kept for the NUL)
    size_t len_ = 0;
    bool err_ = false;
    int depth_ = 0;
    bool first_[kMaxDepth] = {};
    bool afterKey_ = false;
};

/**
 * Cheap structural pre-check of untrusted JSON text: at most `maxValues`
 * values (commas + containers + 1) and nesting <= maxDepth. Strings are
 * skipped correctly (escapes included). It does not validate the JSON.
 */
bool jsonShapeOk(const char* s, size_t n, int maxValues, int maxDepth);

}  // namespace prov
