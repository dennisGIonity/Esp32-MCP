/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/QueryLog.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Record layout (little-endian, kHeader = 20 bytes, then the name):
 *   0 tMs u32 | 4 ip u32 | 8 qtype u16 | 10 latencyMs u16 | 12 rcode | 13 flags
 *   14 category | 15 nameLen | 16 reserved u32 | 20 name[nameLen]
 */
#include "netobserve/QueryLog.h"

#include <string.h>

#include "mcp/os.h"

namespace netobs {
namespace {

void put32(uint8_t* p, uint32_t v) { memcpy(p, &v, 4); }
void put16(uint8_t* p, uint16_t v) { memcpy(p, &v, 2); }
uint32_t get32(const uint8_t* p) {
    uint32_t v;
    memcpy(&v, p, 4);
    return v;
}
uint16_t get16(const uint8_t* p) {
    uint16_t v;
    memcpy(&v, p, 2);
    return v;
}

}  // namespace

QueryLog::~QueryLog() {
    os::freePool(buf_);
    os::freePool(index_);
}

bool QueryLog::begin(size_t bytes, bool preferPsram) {
    if (bytes < 1024 || bytes > 0x7FFFFFFF) return false;
    bytes_ = bytes;
    slots_ = bytes / 24;
    buf_ = static_cast<uint8_t*>(os::allocPool(bytes_, preferPsram));
    index_ = static_cast<Slot*>(os::allocPool(sizeof(Slot) * slots_, preferPsram));
    return buf_ != nullptr && index_ != nullptr;
}

uint32_t QueryLog::oldestSeq() const { return nextSeq_ - static_cast<uint32_t>(count_); }

void QueryLog::dropOldest() {
    if (count_ == 0) return;
    if (oldestSeq() >= sent_) ++lost_;  // never reported
    bytesHeld_ -= index_[head_].len;
    head_ = (head_ + 1) % slots_;
    --count_;
    if (sent_ < oldestSeq()) sent_ = oldestSeq();
}

void QueryLog::push(QueryRecord& rec) {
    if (buf_ == nullptr) return;
    const size_t len = kHeader + rec.nameLen;
    uint32_t at = writeOff_;
    if (at + len > bytes_) at = 0;  // wrap: the tail gap is simply unused
    // Make room: the index must have a free slot and no live record may overlap [at, at+len).
    for (;;) {
        if (count_ == 0) break;
        if (count_ >= slots_) {
            dropOldest();
            continue;
        }
        const Slot& o = index_[head_];
        const bool overlap = o.off < at + len && o.off + o.len > at;
        if (!overlap) break;
        dropOldest();
    }
    uint8_t* p = buf_ + at;
    put32(p, rec.tMs);
    put32(p + 4, rec.ip);
    put16(p + 8, rec.qtype);
    put16(p + 10, rec.latencyMs);
    p[12] = rec.rcode;
    p[13] = rec.flags;
    p[14] = rec.category;
    p[15] = rec.nameLen;
    put32(p + 16, 0);
    memcpy(p + kHeader, rec.name, rec.nameLen);
    const size_t tail = (head_ + count_) % slots_;
    index_[tail] = Slot{at, static_cast<uint16_t>(len)};
    ++count_;
    bytesHeld_ += len;
    rec.seq = nextSeq_++;
    writeOff_ = static_cast<uint32_t>(at + len);
}

void QueryLog::decode(const Slot& s, uint32_t seq, QueryRecord& out) const {
    const uint8_t* p = buf_ + s.off;
    out.seq = seq;
    out.tMs = get32(p);
    out.ip = get32(p + 4);
    out.qtype = get16(p + 8);
    out.latencyMs = get16(p + 10);
    out.rcode = p[12];
    out.flags = p[13];
    out.category = p[14];
    out.nameLen = p[15];
    memcpy(out.name, p + kHeader, out.nameLen);
    out.name[out.nameLen] = '\0';
}

bool QueryLog::get(uint32_t seq, QueryRecord& out) const {
    if (count_ == 0 || seq < oldestSeq() || seq >= nextSeq_) return false;
    const size_t i = seq - oldestSeq();
    decode(index_[(head_ + i) % slots_], seq, out);
    return true;
}

bool QueryLog::newest(size_t i, QueryRecord& out) const {
    if (i >= count_) return false;
    return get(nextSeq_ - 1 - static_cast<uint32_t>(i), out);
}

void QueryLog::markSent(uint32_t upToExclusive) {
    if (upToExclusive > sent_) sent_ = upToExclusive > nextSeq_ ? nextSeq_ : upToExclusive;
}

uint32_t QueryLog::unsent() const {
    const uint32_t from = sent_ > oldestSeq() ? sent_ : oldestSeq();
    return nextSeq_ - from;
}

uint32_t QueryLog::takeLost() {
    const uint32_t n = lost_;
    lost_ = 0;
    return n;
}

uint8_t QueryLog::unsentFillPct() const {
    if (slots_ == 0 || count_ == 0) return 0;
    const size_t n = unsent();
    const size_t byIndex = n * 100 / slots_;
    // Bytes of the unsent records, estimated from the mean record size held.
    const size_t byBytes = (bytesHeld_ * n / count_) * 100 / bytes_;
    const size_t pct = byIndex > byBytes ? byIndex : byBytes;
    return static_cast<uint8_t>(pct > 100 ? 100 : pct);
}

}  // namespace netobs
