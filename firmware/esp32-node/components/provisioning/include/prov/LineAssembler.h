/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/LineAssembler.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Bytes from the UART / USB-Serial-JTAG driver -> complete request lines.
 *
 * MEMORY-SAFETY: the line buffer is a fixed kLineMax + 1 bytes. A line that
 * would overflow it is DISCARDED up to its newline (never truncated and then
 * parsed: a truncated `set` could still be valid JSON with fewer fields) and
 * reported once through onOverlong() (obligation 4: "line too long").
 * The buffer is wiped after every line: it may have held a Wi-Fi password.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace prov {

template <size_t kLineMax>
class LineAssembler {
public:
    /**
     * Feed raw bytes. onLine(const char* line, size_t len) is called for every
     * complete line (without "\r\n", NUL-terminated for convenience);
     * onOverlong() once per discarded line.
     */
    template <typename OnLine, typename OnOverlong>
    void feed(const uint8_t* p, size_t n, OnLine&& onLine, OnOverlong&& onOverlong) {
        for (size_t i = 0; i < n; ++i) {
            const char c = static_cast<char>(p[i]);
            if (c == '\n') {
                if (discarding_) {
                    discarding_ = false;
                } else {
                    size_t len = len_;
                    if (len > 0 && buf_[len - 1] == '\r') --len;
                    buf_[len] = '\0';
                    onLine(buf_, len);
                }
                wipe();
                continue;
            }
            if (discarding_) continue;
            if (len_ >= kLineMax) {  // the line is longer than allowed: drop it whole
                discarding_ = true;
                wipe();
                onOverlong();
                continue;
            }
            buf_[len_++] = c;
        }
    }

    size_t pending() const { return len_; }

private:
    void wipe() {
        memset(buf_, 0, len_ + 1 <= sizeof buf_ ? len_ + 1 : sizeof buf_);
        len_ = 0;
    }

    char buf_[kLineMax + 1] = {};
    size_t len_ = 0;
    bool discarding_ = false;
};

}  // namespace prov
