/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/DnsCache.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "netobserve/DnsCache.h"

#include <string.h>

#include "mcp/os.h"

namespace netobs {

DnsCache::~DnsCache() {
    os::freePool(entries_);
    os::freePool(pool_);
}

bool DnsCache::begin(const Config& cfg) {
    cfg_ = cfg;
    if (cfg.entries == 0 || cfg.entrySize < kDnsHeaderLen + 16) return false;
    entries_ = static_cast<Entry*>(os::allocPool(sizeof(Entry) * cfg.entries, cfg.preferPsram));
    pool_ = static_cast<uint8_t*>(os::allocPool(static_cast<size_t>(cfg.entries) * cfg.entrySize, cfg.preferPsram));
    if (entries_ == nullptr || pool_ == nullptr) return false;
    for (uint16_t i = 0; i < cfg.entries; ++i) {
        entries_[i] = Entry{0, 0, 0, 0, pool_ + static_cast<size_t>(i) * cfg.entrySize};
    }
    return true;
}

uint64_t DnsCache::keyOf(const DnsQuestion& q) {
    // FNV-1a 64 over name | type | class | edns. Collisions are caught by the
    // question comparison on lookup (parseResponse against the stored reply).
    uint64_t h = 1469598103934665603ull;
    auto mix = [&h](uint8_t b) {
        h ^= b;
        h *= 1099511628211ull;
    };
    for (size_t i = 0; i < q.nameLen; ++i) mix(static_cast<uint8_t>(q.name[i]));
    mix(0);
    mix(static_cast<uint8_t>(q.qtype >> 8));
    mix(static_cast<uint8_t>(q.qtype));
    mix(static_cast<uint8_t>(q.qclass >> 8));
    mix(static_cast<uint8_t>(q.qclass));
    mix(q.edns ? 1 : 0);
    return h | 1;  // 0 = empty slot
}

size_t DnsCache::lookup(const uint8_t* query, const DnsQuestion& q, int64_t nowUs, uint8_t* out, size_t cap) {
    if (entries_ == nullptr || !q.nameClean) return 0;
    const uint64_t key = keyOf(q);
    for (uint16_t i = 0; i < cfg_.entries; ++i) {
        Entry& e = entries_[i];
        if (e.key != key) continue;
        if (e.expiresUs <= nowUs) {
            e.key = 0;
            return 0;
        }
        if (e.len > cap) return 0;
        DnsAnswerInfo info{};
        if (!parseResponse(e.resp, e.len, q, info)) {  // hash collision: a different question
            return 0;
        }
        memcpy(out, e.resp, e.len);
        // This client's id and question bytes (same length: the names differ in case only).
        out[0] = query[0];
        out[1] = query[1];
        memcpy(out + kDnsHeaderLen, query + kDnsHeaderLen, q.nameWireLen);
        const uint32_t elapsedS = static_cast<uint32_t>((nowUs - e.storedUs) / 1000000);
        if (elapsedS > 0) ageTtls(out, e.len, elapsedS);
        return e.len;
    }
    return 0;
}

void DnsCache::store(const DnsQuestion& q, const uint8_t* resp, size_t len, const DnsAnswerInfo& info, int64_t nowUs) {
    if (entries_ == nullptr || !q.nameClean || info.rcode != 0 || info.truncated || info.ancount == 0) return;
    if (len > cfg_.entrySize || q.qclass != kClassIn) return;
    uint32_t ttl = info.minTtl;
    if (ttl < cfg_.minTtlS) ttl = cfg_.minTtlS;
    if (ttl > cfg_.maxTtlS) ttl = cfg_.maxTtlS;
    const uint64_t key = keyOf(q);
    // Same key, an empty/expired slot, else the one closest to expiry.
    Entry* slot = nullptr;
    for (uint16_t i = 0; i < cfg_.entries && slot == nullptr; ++i) {
        if (entries_[i].key == key) slot = &entries_[i];
    }
    for (uint16_t i = 0; i < cfg_.entries && slot == nullptr; ++i) {
        if (entries_[i].key == 0 || entries_[i].expiresUs <= nowUs) slot = &entries_[i];
    }
    if (slot == nullptr) {
        slot = &entries_[0];
        for (uint16_t i = 1; i < cfg_.entries; ++i) {
            if (entries_[i].expiresUs < slot->expiresUs) slot = &entries_[i];
        }
    }
    memcpy(slot->resp, resp, len);
    slot->len = static_cast<uint16_t>(len);
    slot->key = key;
    slot->storedUs = nowUs;
    slot->expiresUs = nowUs + static_cast<int64_t>(ttl) * 1000000;
}

uint16_t DnsCache::live(int64_t nowUs) const {
    uint16_t n = 0;
    for (uint16_t i = 0; entries_ != nullptr && i < cfg_.entries; ++i) {
        if (entries_[i].key != 0 && entries_[i].expiresUs > nowUs) ++n;
    }
    return n;
}

uint16_t DnsCache::flush(int64_t nowUs) {
    const uint16_t n = live(nowUs);
    for (uint16_t i = 0; entries_ != nullptr && i < cfg_.entries; ++i) entries_[i].key = 0;
    return n;
}

}  // namespace netobs
