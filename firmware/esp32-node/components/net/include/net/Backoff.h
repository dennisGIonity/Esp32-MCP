/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | net/Backoff.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Reconnect delay: base, 2x base, 4x base ... capped, each +/- jitterPct %
 * (so a building full of boards does not hit the broker in the same second
 * after a power cut), never above the cap. Pure and header-only: the MQTT
 * link uses it on the device, test/host/test_backoff.cpp checks it.
 *
 *   Backoff b;                       // 1 s .. 60 s, +/- 20 %
 *   delayMs = b.next(esp_random());  // on every failed / lost connection
 *   b.reset();                       // on CONNECTED
 */
#pragma once

#include <stdint.h>

namespace net {

class Backoff {
public:
    constexpr Backoff(uint32_t baseMs = 1000, uint32_t capMs = 60000, uint8_t jitterPct = 20)
        : base_(baseMs), cap_(capMs < baseMs ? baseMs : capMs), jitter_(jitterPct > 100 ? 100 : jitterPct) {}

    /** The nominal delay of the next attempt, before jitter. */
    uint32_t nominal() const {
        uint64_t d = base_;
        for (uint8_t i = 0; i < attempt_ && d < cap_; ++i) d *= 2;
        return d > cap_ ? cap_ : static_cast<uint32_t>(d);
    }

    /** Delay before the next attempt; `rnd` is any 32-bit random value. Advances the attempt. */
    uint32_t next(uint32_t rnd) {
        const uint32_t d = nominal();
        if (attempt_ < 31) ++attempt_;
        const uint32_t span = static_cast<uint32_t>(static_cast<uint64_t>(d) * jitter_ / 100u);
        const uint64_t lo = d - span;
        const uint64_t v = lo + (span == 0 ? 0 : rnd % (2ull * span + 1ull));
        return v > cap_ ? cap_ : static_cast<uint32_t>(v);
    }

    void reset() { attempt_ = 0; }
    uint8_t attempt() const { return attempt_; }
    uint32_t baseMs() const { return base_; }
    uint32_t capMs() const { return cap_; }

private:
    uint32_t base_;
    uint32_t cap_;
    uint8_t jitter_;
    uint8_t attempt_ = 0;
};

}  // namespace net
