/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/Validate.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Every function here reads exactly `n` bytes of untrusted input and writes
 * only into fixed buffers whose size it checks first.
 */
#include "prov/Validate.h"

#include <stdio.h>
#include <string.h>

#include "ewp/cbor.h"  // ewp::utf8_valid

namespace prov {
namespace {

bool isDigit(char c) { return c >= '0' && c <= '9'; }
bool isHex(char c) { return isDigit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }
bool isLowerAlnum(char c) { return isDigit(c) || (c >= 'a' && c <= 'z'); }
char lower(char c) { return (c >= 'A' && c <= 'Z') ? static_cast<char>(c - 'A' + 'a') : c; }
bool isSpace(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\f' || c == '\v'; }

void trim(const char*& s, size_t& n) {
    while (n > 0 && isSpace(s[0])) {
        ++s;
        --n;
    }
    while (n > 0 && isSpace(s[n - 1])) --n;
}

/** One IPv4 octet per IPV4_RE: 0-255 without leading zeros. */
bool ipv4Octet(const char* s, size_t n) {
    if (n == 0 || n > 3) return false;
    for (size_t i = 0; i < n; ++i) {
        if (!isDigit(s[i])) return false;
    }
    if (n > 1 && s[0] == '0') return false;
    int v = 0;
    for (size_t i = 0; i < n; ++i) v = v * 10 + (s[i] - '0');
    return v <= 255;
}

/** host-url.ts validHostname(): all-numeric -> real IPv4; else DNS labels [a-z0-9-]{1,63}, no edge '-'. */
bool validHostname(const char* h, size_t n) {
    if (n == 0 || n > 253) return false;
    bool allNumeric = true;
    size_t labels = 0;
    for (size_t start = 0; start <= n;) {
        size_t end = start;
        while (end < n && h[end] != '.') ++end;
        const size_t len = end - start;
        ++labels;
        bool numeric = len > 0;
        for (size_t i = start; i < end; ++i) {
            if (!isDigit(h[i])) numeric = false;
        }
        if (!numeric) allNumeric = false;
        start = end + 1;
        if (end == n) break;
    }
    if (allNumeric) {
        if (labels != 4) return false;
        for (size_t start = 0; start <= n;) {
            size_t end = start;
            while (end < n && h[end] != '.') ++end;
            if (!ipv4Octet(h + start, end - start)) return false;
            start = end + 1;
            if (end == n) break;
        }
        return true;
    }
    for (size_t start = 0; start <= n;) {
        size_t end = start;
        while (end < n && h[end] != '.') ++end;
        const size_t len = end - start;
        if (len < 1 || len > 63 || h[start] == '-' || h[end - 1] == '-') return false;
        for (size_t i = start; i < end; ++i) {
            if (!isLowerAlnum(h[i]) && h[i] != '-') return false;
        }
        start = end + 1;
        if (end == n) break;
    }
    return true;
}

bool copyOut(char* out, size_t cap, const char* s, size_t n) {
    if (n + 1 > cap) return false;
    memcpy(out, s, n);
    out[n] = '\0';
    return true;
}

}  // namespace

bool validUtf8(const char* s, size_t n) { return ewp::utf8_valid(s, n); }

bool hasControlChars(const char* s, size_t n) {
    const uint8_t* p = reinterpret_cast<const uint8_t*>(s);
    for (size_t i = 0; i < n; ++i) {
        if (p[i] < 0x20 || p[i] == 0x7F) return true;
        if (p[i] == 0xC2 && i + 1 < n && p[i + 1] >= 0x80 && p[i + 1] <= 0x9F) return true;  // U+0080..U+009F
    }
    return false;
}

bool validSsid(const char* s, size_t n, const char** err) {
    if (n < 1 || n > Limits::kSsid) {
        *err = "ssid must be 1-32 bytes";
        return false;
    }
    if (!validUtf8(s, n) || hasControlChars(s, n)) {
        *err = "ssid contains control characters or invalid UTF-8";
        return false;
    }
    return true;
}

bool validWifiPass(const char* s, size_t n, const char** err) {
    if (n == 0) return true;  // open network
    if (n == Limits::kPskHex) {
        for (size_t i = 0; i < n; ++i) {
            if (!isHex(s[i])) {
                *err = "a 64-character password must be a hex PSK";
                return false;
            }
        }
        return true;
    }
    if (n < Limits::kPassMin || n > Limits::kPassMax) {
        *err = "WPA2 password must be 8-63 chars (or empty for open)";
        return false;
    }
    if (!validUtf8(s, n) || hasControlChars(s, n)) {
        *err = "password contains control characters or invalid UTF-8";
        return false;
    }
    return true;
}

bool validTopicLevel(const char* s, size_t n) {
    if (n < 1 || n > Limits::kTopicLevel || !isLowerAlnum(s[0])) return false;
    for (size_t i = 1; i < n; ++i) {
        if (!isLowerAlnum(s[i]) && s[i] != '-') return false;
    }
    return true;
}

bool validToken(const char* s, size_t n) {
    if (n < Limits::kTokenMin || n > Limits::kSecret) return false;
    for (size_t i = 0; i < n; ++i) {
        const char c = s[i];
        const bool ok = isDigit(c) || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '.' || c == '_' ||
                        c == '~' || c == '-';
        if (!ok) return false;
    }
    return true;
}

bool validText(const char* s, size_t n, size_t maxBytes, const char** err) {
    if (n > maxBytes) {
        *err = "too long";
        return false;
    }
    if (!validUtf8(s, n) || hasControlChars(s, n)) {
        *err = "contains control characters or invalid UTF-8";
        return false;
    }
    return true;
}

bool normalizeUuid(const char* s, size_t n, char out[37]) {
    trim(s, n);
    if (n >= 2 && s[0] == '{' && s[n - 1] == '}') {
        ++s;
        n -= 2;
    }
    if (n != Limits::kUuid) return false;
    for (size_t i = 0; i < n; ++i) {
        const char c = lower(s[i]);
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (c != '-') return false;
        } else if (!isHex(c)) {
            return false;
        }
        out[i] = c;
    }
    out[36] = '\0';
    if (out[14] < '1' || out[14] > '8') return false;  // version 1..8
    const char v = out[19];
    if (v != '8' && v != '9' && v != 'a' && v != 'b') return false;  // variant 10xx
    return true;
}

bool validPemCert(const char* s, size_t n, const char** err) {
    static const char kBegin[] = "-----BEGIN CERTIFICATE-----";
    static const char kEnd[] = "-----END CERTIFICATE-----";
    const size_t nb = sizeof kBegin - 1;
    const size_t ne = sizeof kEnd - 1;
    if (n > Limits::kCaPem) {
        *err = "CA certificate is limited to 3072 bytes";
        return false;
    }
    while (n > 0 && isSpace(s[n - 1])) --n;
    *err = "expected one PEM certificate (-----BEGIN CERTIFICATE----- ... -----END CERTIFICATE-----)";
    if (n < nb + 1 + 1 + ne || memcmp(s, kBegin, nb) != 0 || memcmp(s + n - ne, kEnd, ne) != 0) return false;
    size_t i = nb;
    if (s[i] == '\r') ++i;
    if (s[i] != '\n') return false;
    ++i;
    const size_t bodyEnd = n - ne;
    if (i >= bodyEnd) return false;
    for (; i < bodyEnd; ++i) {
        const char c = s[i];
        const bool ok = isDigit(c) || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '+' || c == '/' ||
                        c == '=' || c == '\r' || c == '\n';
        if (!ok) return false;  // also stops a second "-----BEGIN" block
    }
    *err = nullptr;
    return true;
}

void sanitizeTopicLevel(const char* in, char* out, size_t outCap, const char* fallback) {
    size_t o = 0;
    const size_t max = outCap > 0 ? (outCap - 1 < Limits::kTopicLevel ? outCap - 1 : Limits::kTopicLevel) : 0;
    for (size_t i = 0; in != nullptr && in[i] != '\0' && o < max; ++i) {
        char c = lower(in[i]);
        if (!isLowerAlnum(c) && c != '-') c = '-';
        if (o == 0 && c == '-') continue;  // must start with [a-z0-9]
        out[o++] = c;
    }
    if (outCap > 0) out[o] = '\0';
    if (o == 0 && outCap > 0) snprintf(out, outCap, "%s", fallback);
}

bool parseHostUrl(const char* s, size_t n, HostUrl& out, const char** err) {
    out = HostUrl{};
    trim(s, n);
    if (n == 0) {
        *err = "empty";
        return false;
    }
    if (n > Limits::kHostUrl) {
        *err = "longer than 128 bytes";
        return false;
    }
    char work[Limits::kHostUrl + 8];
    size_t wn;
    bool hasScheme = false;
    for (size_t i = 0; i + 2 < n; ++i) {
        if (s[i] == ':' && s[i + 1] == '/' && s[i + 2] == '/') {
            hasScheme = true;
            break;
        }
    }
    if (hasScheme) {
        memcpy(work, s, n);
        wn = n;
    } else {
        memcpy(work, "mqtt://", 7);  // a bare host is what the old flasher's "server" box meant
        memcpy(work + 7, s, n);
        wn = n + 7;
    }
    const char* const kNotUrl = "not a URL like mqtt://host:1883";

    // scheme: [a-z][a-z0-9+.-]* "://"
    size_t i = 0;
    char scheme[8];
    size_t sl = 0;
    const char first = lower(work[0]);
    if (!(first >= 'a' && first <= 'z')) {
        *err = kNotUrl;
        return false;
    }
    while (i < wn && work[i] != ':') {
        const char c = lower(work[i]);
        if (!(isLowerAlnum(c) || c == '+' || c == '.' || c == '-')) {
            *err = kNotUrl;
            return false;
        }
        if (sl < sizeof scheme - 1) scheme[sl] = c;
        ++sl;
        ++i;
    }
    if (i + 2 >= wn || work[i + 1] != '/' || work[i + 2] != '/') {
        *err = kNotUrl;
        return false;
    }
    i += 3;
    scheme[sl < sizeof scheme ? sl : sizeof scheme - 1] = '\0';
    uint16_t defPort;
    if (sl == 4 && strcmp(scheme, "mqtt") == 0) {
        out.scheme = Scheme::Mqtt;
        defPort = 1883;
    } else if (sl == 5 && strcmp(scheme, "mqtts") == 0) {
        out.scheme = Scheme::Mqtts;
        defPort = 8883;
    } else if (sl == 2 && strcmp(scheme, "ws") == 0) {
        out.scheme = Scheme::Ws;
        defPort = 80;
    } else if (sl == 3 && strcmp(scheme, "wss") == 0) {
        out.scheme = Scheme::Wss;
        defPort = 443;
    } else if (sl == 4 && strcmp(scheme, "http") == 0) {
        out.scheme = Scheme::Http;
        defPort = 8099;
    } else {
        *err = "unsupported scheme (use mqtt, mqtts, ws, wss or http)";
        return false;
    }
    out.tls = out.scheme == Scheme::Mqtts || out.scheme == Scheme::Wss;

    // authority: up to '/', '?' or '#'
    const size_t authStart = i;
    while (i < wn && work[i] != '/' && work[i] != '?' && work[i] != '#') ++i;
    const size_t authEnd = i;
    for (size_t k = authStart; k < authEnd; ++k) {
        if (work[k] == '@') {
            *err = "remove the user:password@ part - use the MQTT user and password fields";
            return false;
        }
        if (isSpace(work[k])) {
            *err = kNotUrl;
            return false;
        }
    }
    size_t hostEnd;
    if (authStart < authEnd && work[authStart] == '[') {
        hostEnd = authStart;
        while (hostEnd < authEnd && work[hostEnd] != ']') ++hostEnd;
        if (hostEnd == authEnd) {
            *err = "invalid IPv6 literal";
            return false;
        }
        ++hostEnd;  // include ']'
        bool colon = false;
        for (size_t k = authStart + 1; k + 1 < hostEnd; ++k) {
            const char c = lower(work[k]);
            if (c == ':') colon = true;
            if (!(isHex(c) || c == ':' || c == '.')) {
                *err = "invalid IPv6 literal";
                return false;
            }
        }
        if (!colon || hostEnd - authStart < 3) {
            *err = "invalid IPv6 literal";
            return false;
        }
    } else {
        hostEnd = authStart;
        while (hostEnd < authEnd && work[hostEnd] != ':') ++hostEnd;
    }
    const size_t hostLen = hostEnd - authStart;
    if (hostLen == 0 || hostLen >= sizeof out.host) {
        *err = kNotUrl;
        return false;
    }
    for (size_t k = 0; k < hostLen; ++k) out.host[k] = lower(work[authStart + k]);
    out.host[hostLen] = '\0';
    if (out.host[0] != '[' && !validHostname(out.host, hostLen)) {
        *err = "not a valid host name or IPv4 address";
        return false;
    }

    // port
    out.port = defPort;
    if (hostEnd < authEnd) {
        if (work[hostEnd] != ':' || hostEnd + 1 == authEnd || authEnd - hostEnd - 1 > 5) {
            *err = kNotUrl;
            return false;
        }
        uint32_t p = 0;
        for (size_t k = hostEnd + 1; k < authEnd; ++k) {
            if (!isDigit(work[k])) {
                *err = kNotUrl;
                return false;
            }
            p = p * 10 + static_cast<uint32_t>(work[k] - '0');
        }
        if (p < 1 || p > 65535) {
            *err = "port must be 1-65535";
            return false;
        }
        out.port = static_cast<uint16_t>(p);
    }

    // path (ws/wss only), no query or fragment
    const size_t pathStart = i;
    while (i < wn && work[i] != '?' && work[i] != '#') {
        if (isSpace(work[i])) {
            *err = kNotUrl;
            return false;
        }
        ++i;
    }
    if (i < wn) {
        *err = "query strings and #fragments are not allowed";
        return false;
    }
    const size_t pathLen = i - pathStart;
    if (pathLen > 1 || (pathLen == 1 && work[pathStart] != '/')) {
        if (out.scheme != Scheme::Ws && out.scheme != Scheme::Wss) {
            *err = "a path is only meaningful for ws:// and wss://";
            return false;
        }
        if (!copyOut(out.path, sizeof out.path, work + pathStart, pathLen)) {
            *err = "longer than 128 bytes";
            return false;
        }
    }

    const int w = snprintf(out.href, sizeof out.href, "%s://%s:%u%s", scheme, out.host, static_cast<unsigned>(out.port),
                           out.path);
    if (w < 0 || static_cast<size_t>(w) >= sizeof out.href) {
        *err = "longer than 128 bytes";
        return false;
    }
    *err = nullptr;
    return true;
}

}  // namespace prov
