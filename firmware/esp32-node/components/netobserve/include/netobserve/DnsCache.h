/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/DnsCache.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * A small positive-answer cache for the LAN forwarder: repeat lookups (every
 * browser tab asks for the same CDN names) are answered locally, which keeps
 * the forwarder's upstream traffic and latency down.
 *
 *  - Key: lower-case name + type + class + "query had EDNS" (a cached reply
 *    with an OPT record is never handed to a client that did not ask for one).
 *  - Only NOERROR replies with answers, not truncated, <= entrySize bytes.
 *  - Lifetime: the smallest answer TTL, clamped to [minTtlS, maxTtlS].
 *  - On a hit the reply is rewritten for THIS client: its id, its question
 *    bytes (letter case - DNS 0x20 randomisation) and TTLs aged by the time
 *    spent in the cache.
 *
 * MEMORY: entries x entrySize bytes allocated once (PSRAM when present).
 * THREADS: none here - NetObserve guards it with its mutex.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "netobserve/DnsWire.h"

namespace netobs {

class DnsCache {
public:
    struct Config {
        uint16_t entries = 48;
        uint16_t entrySize = 512;
        uint32_t minTtlS = 15;
        uint32_t maxTtlS = 3600;
        bool preferPsram = true;
    };

    DnsCache() = default;
    ~DnsCache();
    DnsCache(const DnsCache&) = delete;
    DnsCache& operator=(const DnsCache&) = delete;

    bool begin(const Config& cfg);

    /**
     * A cached reply for `q` (the client's query bytes in `query`), written to
     * out. Returns its length, 0 on a miss.
     */
    size_t lookup(const uint8_t* query, const DnsQuestion& q, int64_t nowUs, uint8_t* out, size_t cap);

    /** Remember `resp` (already validated as the answer to q) if it is cacheable. */
    void store(const DnsQuestion& q, const uint8_t* resp, size_t len, const DnsAnswerInfo& info, int64_t nowUs);

    /** Drop everything; returns how many live entries there were. */
    uint16_t flush(int64_t nowUs);
    uint16_t live(int64_t nowUs) const;

private:
    struct Entry {
        uint64_t key;
        int64_t storedUs;
        int64_t expiresUs;
        uint16_t len;
        uint8_t* resp;
    };
    static uint64_t keyOf(const DnsQuestion& q);

    Config cfg_;
    Entry* entries_ = nullptr;
    uint8_t* pool_ = nullptr;
};

}  // namespace netobs
