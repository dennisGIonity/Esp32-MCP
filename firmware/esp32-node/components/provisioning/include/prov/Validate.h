/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/Validate.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The `set` field rules - the firmware's copy of the checks the portal makes
 * in packages/provisioning (schema.ts, host-url.ts, uuid.ts, fields.ts), so a
 * board rejects bad input even from a tool that skipped them. All functions
 * take (pointer, length): the input comes from a parsed JSON string and is
 * NOT assumed to be NUL-terminated or valid UTF-8.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace prov {

/** Byte limits (fields.ts LIMITS). Buffers are these + 1 for the NUL. */
struct Limits {
    static constexpr size_t kSsid = 32;
    static constexpr size_t kPassMin = 8;
    static constexpr size_t kPassMax = 63;
    static constexpr size_t kPskHex = 64;
    static constexpr size_t kHostUrl = 128;
    static constexpr size_t kLabel = 48;
    static constexpr size_t kUser = 64;
    static constexpr size_t kSecret = 128;
    static constexpr size_t kTokenMin = 16;
    static constexpr size_t kCaPem = 3072;
    static constexpr size_t kTopicLevel = 32;
    static constexpr size_t kUuid = 36;
};

/** C0 controls, DEL and C1 controls (U+0080..U+009F, i.e. 0xC2 0x80..0x9F in UTF-8). */
bool hasControlChars(const char* s, size_t n);

/** Strict UTF-8 (no overlongs, no surrogates). */
bool validUtf8(const char* s, size_t n);

/** 1-32 bytes, UTF-8, no control characters. */
bool validSsid(const char* s, size_t n, const char** err);

/** "" (open network) | 8-63 bytes without control characters | exactly 64 hex digits (raw PSK). */
bool validWifiPass(const char* s, size_t n, const char** err);

/** MQTT topic level: ^[a-z0-9][a-z0-9-]{0,31}$ (also @ionity/ewp siteId()). */
bool validTopicLevel(const char* s, size_t n);

/** Tokens: [A-Za-z0-9._~-]{16,128}. */
bool validToken(const char* s, size_t n);

/** Free text (mqtt_user, mqtt_pass, ota_pass, label): <= max bytes, UTF-8, no control characters. */
bool validText(const char* s, size_t n, size_t maxBytes, const char** err);

/**
 * RFC 9562 UUID -> canonical lowercase "8-4-4-4-12" in out (37 bytes incl. NUL).
 * Accepts upper case, surrounding whitespace and {braces}; version 1-8, variant 10xx.
 */
bool normalizeUuid(const char* s, size_t n, char out[37]);

/** One PEM certificate, <= 3072 bytes, base64 body only (one block). Structure only. */
bool validPemCert(const char* s, size_t n, const char** err);

/** fw 2.x had no rules for site/group: make a valid topic level out of anything. */
void sanitizeTopicLevel(const char* in, char* out, size_t outCap, const char* fallback);

enum class Scheme : uint8_t { Mqtt, Mqtts, Ws, Wss, Http };

struct HostUrl {
    Scheme scheme = Scheme::Mqtt;
    char host[130] = {};   // lowercase DNS name, IPv4, or [IPv6] (brackets kept)
    uint16_t port = 0;
    char path[130] = {};   // "" except ws/wss
    char href[160] = {};   // canonical scheme://host:port[/path], port always written (input <= 128 B + default port)
    bool tls = false;
};

/**
 * host-url.ts parseHostUrl(): a bare host means mqtt://, the port defaults
 * per scheme (http -> 8099, the fleet host's API port), user:pass@ and
 * ?query/#fragment are refused, a path only for ws/wss. Output is canonical.
 */
bool parseHostUrl(const char* s, size_t n, HostUrl& out, const char** err);

/** Schemes this firmware can actually connect with (Phase 3: MQTT over TCP or TLS). */
inline bool hostUrlSupported(const HostUrl& u) { return u.scheme == Scheme::Mqtt || u.scheme == Scheme::Mqtts; }

}  // namespace prov
