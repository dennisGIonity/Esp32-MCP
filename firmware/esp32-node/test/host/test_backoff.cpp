/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | host tests: MQTT reconnect back-off
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 */
#include "check.h"
#include "net/Backoff.h"

TEST(backoff_doubles_to_the_cap) {
    net::Backoff b(1000, 60000, 0);  // no jitter: exact sequence
    const uint32_t want[] = {1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000, 60000};
    for (uint32_t w : want) CHECK_EQ(b.next(12345), w);
    for (int i = 0; i < 100; ++i) CHECK_EQ(b.next(static_cast<uint32_t>(i)), 60000u);  // attempt counter cannot overflow
}

TEST(backoff_jitter_stays_in_bounds_and_varies) {
    net::Backoff b;  // 1 s .. 60 s, +/- 20 %
    uint32_t lo = UINT32_MAX;
    uint32_t hi = 0;
    for (uint32_t r = 0; r < 2000; ++r) {
        net::Backoff one;
        const uint32_t d = one.next(r * 2654435761u);
        CHECK(d >= 800 && d <= 1200);
        lo = d < lo ? d : lo;
        hi = d > hi ? d : hi;
    }
    CHECK(lo < 850 && hi > 1150);  // the whole +/- 20 % range is used
    for (int i = 0; i < 12; ++i) b.next(0xFFFFFFFFu);
    for (uint32_t r = 0; r < 500; ++r) {
        const uint32_t d = b.next(r * 2246822519u);
        CHECK(d >= 48000 && d <= 60000);  // capped even with +20 % jitter
    }
}

TEST(backoff_reset_and_odd_configs) {
    net::Backoff b;
    b.next(1);
    b.next(1);
    CHECK_EQ(b.attempt(), 2u);
    CHECK_EQ(b.nominal(), 4000u);
    b.reset();
    CHECK_EQ(b.attempt(), 0u);
    CHECK_EQ(b.nominal(), 1000u);
    net::Backoff tiny(5, 1, 100);  // cap below base becomes base; 100 % jitter is 0 .. 2x, capped
    for (uint32_t r = 0; r < 50; ++r) CHECK(tiny.next(r) <= 5u);
}
