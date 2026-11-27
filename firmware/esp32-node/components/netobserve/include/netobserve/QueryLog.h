/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/QueryLog.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The node's memory of recent DNS queries: a byte ring of variable-length
 * records (a 20-byte header + the name), so a short name costs ~35 bytes and
 * a 253-byte one still fits. Feeds the DNS_BATCH events (oldest first, from a
 * cursor) and query_dns_log (newest first).
 *
 * When the ring is full the oldest records are overwritten; records that
 * were never sent in a batch are counted in `lost()` (DnsBatch.dropped).
 *
 * MEMORY: `bytes` allocated once (PSRAM when present) + an index of
 * bytes / 24 record slots. THREADS: none here - NetObserve's mutex.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "netobserve/DnsWire.h"

namespace netobs {

struct QueryRecord {
    uint32_t seq;        // per-boot, monotonic
    uint32_t tMs;        // uptime ms when the query was answered
    uint32_t ip;         // client IPv4, host order
    uint16_t qtype;
    uint16_t latencyMs;  // 0 for cache hits
    uint8_t rcode;
    uint8_t flags;       // EWP_DNSQ_* bitset
    uint8_t category;    // watchlist category, 0 = none
    uint8_t nameLen;
    char name[kMaxNameLen + 1];
};

class QueryLog {
public:
    QueryLog() = default;
    ~QueryLog();
    QueryLog(const QueryLog&) = delete;
    QueryLog& operator=(const QueryLog&) = delete;

    bool begin(size_t bytes, bool preferPsram);

    /** Append (overwriting the oldest records when needed). Assigns rec.seq. */
    void push(QueryRecord& rec);

    /** Records currently held, and the sequence range [oldestSeq, nextSeq). */
    size_t count() const { return count_; }
    uint32_t oldestSeq() const;
    uint32_t nextSeq() const { return nextSeq_; }
    /** Fill `out` with the record of sequence `seq`; false when it was overwritten or not written yet. */
    bool get(uint32_t seq, QueryRecord& out) const;
    /** i-th newest record (0 = newest). */
    bool newest(size_t i, QueryRecord& out) const;

    /** Batch cursor: records with seq >= sent() have not been reported yet. */
    uint32_t sent() const { return sent_; }
    void markSent(uint32_t upToExclusive);
    uint32_t unsent() const;
    /** Records overwritten before they were reported (cleared by takeLost()). */
    uint32_t takeLost();
    /** How full the ring is with UNSENT records, 0..100 % (index or bytes, whichever is fuller). */
    uint8_t unsentFillPct() const;
    size_t capacityRecords() const { return slots_; }

private:
    static constexpr size_t kHeader = 20;
    struct Slot {
        uint32_t off;
        uint16_t len;
    };
    void dropOldest();
    void decode(const Slot& s, uint32_t seq, QueryRecord& out) const;

    uint8_t* buf_ = nullptr;
    size_t bytes_ = 0;
    Slot* index_ = nullptr;
    size_t slots_ = 0;
    size_t head_ = 0;    // index of the oldest slot
    size_t count_ = 0;
    uint32_t writeOff_ = 0;
    uint32_t nextSeq_ = 1;
    uint32_t sent_ = 1;
    uint32_t lost_ = 0;
    size_t bytesHeld_ = 0;
};

}  // namespace netobs
