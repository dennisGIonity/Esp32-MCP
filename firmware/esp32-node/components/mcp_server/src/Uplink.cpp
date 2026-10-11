/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | mcp/Uplink.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "mcp/Uplink.h"

namespace mcp {

bool Uplink::begin(size_t slots, size_t slotSize, bool preferPsram) {
    if (pool_ != nullptr || slots == 0 || slots > kMaxSlots || slotSize < EWP_HEADER_LEN + 16 ||
        slotSize > 0xFFFFu) {
        return false;
    }
    pool_ = static_cast<uint8_t*>(os::allocPool(slots * slotSize, preferPsram));
    if (pool_ == nullptr) return false;
    if (!free_.begin(slots) || !tx_.begin(slots)) return false;
    slots_ = slots;
    slotSize_ = slotSize;
    for (size_t i = 0; i < slots; ++i) {
        const uint8_t idx = static_cast<uint8_t>(i);
        free_.send(idx, 0);  // capacity == slots: cannot fail
    }
    return true;
}

int Uplink::acquire(uint32_t timeoutMs) {
    if (pool_ == nullptr) return -1;
    uint8_t idx;
    if (!free_.receive(idx, timeoutMs)) return -1;
    return idx;
}

uint8_t* Uplink::buffer(int slot) const {
    // MEMORY-SAFETY: callers only pass indices they got from acquire()/nextDue().
    if (pool_ == nullptr || slot < 0 || static_cast<size_t>(slot) >= slots_) return nullptr;
    return pool_ + static_cast<size_t>(slot) * slotSize_;
}

void Uplink::release(int slot) {
    if (slot < 0 || static_cast<size_t>(slot) >= slots_) return;
    const uint8_t idx = static_cast<uint8_t>(slot);
    free_.send(idx, 0);  // capacity == slots and each index is owned once: cannot fail
}

bool Uplink::submit(int slot, size_t len, int64_t notBeforeUs, uint32_t timeoutMs) {
    uint8_t* p = buffer(slot);
    if (p == nullptr) return false;
    ewp::RawHeader raw;
    const bool sane = len >= EWP_HEADER_LEN && len <= slotSize_ && ewp::decodeHeader(p, len, raw) &&
                      raw.magic == EWP_MAGIC && raw.version == EWP_VERSION &&
                      static_cast<size_t>(EWP_HEADER_LEN) + raw.h.bodyLen == len;
    const ewp::Channel ch = sane ? ewp::channelOf(raw.h.type) : ewp::Channel::Invalid;
    if (ch == ewp::Channel::Invalid) {
        release(slot);
        noteDrop();
        return false;
    }
    TxItem item;
    item.notBeforeUs = notBeforeUs;
    item.len = static_cast<uint16_t>(len);
    item.slot = static_cast<uint8_t>(slot);
    item.channel = static_cast<uint8_t>(ch);
    if (!tx_.send(item, timeoutMs)) {
        release(slot);
        noteDrop();
        return false;
    }
    return true;
}

bool Uplink::takeDueParked(TxItem& out, int64_t now) {
    for (size_t i = 0; i < parkedN_; ++i) {
        if (parked_[i].notBeforeUs <= now) {
            out = parked_[i];
            // keep FIFO order among parked frames (ACK before RES of one rid)
            for (size_t k = i + 1; k < parkedN_; ++k) parked_[k - 1] = parked_[k];
            --parkedN_;
            parkedCount_.store(static_cast<uint32_t>(parkedN_), std::memory_order_relaxed);
            return true;
        }
    }
    return false;
}

bool Uplink::nextDue(TxItem& out, uint32_t maxWaitMs) {
    int64_t now = os::nowUs();
    if (takeDueParked(out, now)) return true;

    uint32_t waitMs = maxWaitMs;
    for (size_t i = 0; i < parkedN_; ++i) {
        const int64_t dueInUs = parked_[i].notBeforeUs - now;
        const uint32_t dueInMs = dueInUs <= 0 ? 0u : static_cast<uint32_t>((dueInUs + 999) / 1000);
        if (dueInMs < waitMs) waitMs = dueInMs;
    }

    TxItem item;
    if (!tx_.receive(item, waitMs)) return takeDueParked(out, os::nowUs());

    now = os::nowUs();
    if (parkedN_ == 0 && item.notBeforeUs <= now) {  // fast path: nothing parked, due now
        out = item;
        return true;
    }
    // While anything is parked, every new frame goes through the parking lot so
    // frames of one rid (same notBefore) leave in submission order: ACK, then RES.
    if (parkedN_ >= kMaxSlots) {  // impossible (at most slots_ frames exist) - never lose one
        out = item;
        return true;
    }
    parked_[parkedN_++] = item;
    parkedCount_.store(static_cast<uint32_t>(parkedN_), std::memory_order_relaxed);
    return takeDueParked(out, now);
}

}  // namespace mcp
