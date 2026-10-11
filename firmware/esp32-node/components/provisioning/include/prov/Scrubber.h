/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/Scrubber.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Defence in depth for obligation 7 ("never echo or log a secret"): our own
 * code never formats a secret, but a third-party component could print a URI
 * or a config struct. Every console log line passes through scrub(), which
 * replaces any stored secret (>= 6 bytes, shorter ones would hit ordinary
 * words) with "***" before a byte leaves the board.
 *
 * Keeps its own COPIES of the secrets: the config may be rewritten by `set`
 * while another task logs. Callers serialise access (the console mutex).
 */
#pragma once

#include <stddef.h>
#include <string.h>

namespace prov {

class Scrubber {
public:
    static constexpr size_t kMaxSecrets = 6;
    static constexpr size_t kMaxLen = 128;
    static constexpr size_t kMinLen = 6;

    void clear() {
        for (auto& s : secrets_) volatileZero(s, sizeof s);
        count_ = 0;
    }

    /** Ignores values that are too short to scrub safely or too long to store. */
    void add(const char* secret) {
        const size_t n = secret ? strnlen(secret, kMaxLen + 1) : 0;
        if (n < kMinLen || n > kMaxLen || count_ >= kMaxSecrets) return;
        memcpy(secrets_[count_], secret, n);
        secrets_[count_][n] = '\0';
        ++count_;
    }

    /** In-place: each occurrence becomes "***" (the line can only get shorter). Returns the new length. */
    size_t scrub(char* line, size_t len) const {
        for (size_t s = 0; s < count_; ++s) {
            const size_t n = strlen(secrets_[s]);
            size_t i = 0;
            while (i + n <= len) {
                if (memcmp(line + i, secrets_[s], n) == 0) {
                    memcpy(line + i, "***", 3);
                    memmove(line + i + 3, line + i + n, len - i - n);
                    len -= n - 3;
                    i += 3;
                } else {
                    ++i;
                }
            }
        }
        return len;
    }

    size_t count() const { return count_; }

private:
    static void volatileZero(char* p, size_t n) {
        volatile char* v = p;
        while (n--) *v++ = 0;
    }
    char secrets_[kMaxSecrets][kMaxLen + 1] = {};
    size_t count_ = 0;
};

}  // namespace prov
