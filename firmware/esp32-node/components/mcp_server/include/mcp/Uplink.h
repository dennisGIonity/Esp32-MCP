/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | mcp/Uplink.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The device -> gateway frame pool and the single-consumer transmit queue.
 *
 *   producers (any task)                       consumer (net_task ONLY)
 *   ---------------------                      ------------------------
 *   slot = acquire(timeout)    free_q ---->
 *   write header + CBOR into buffer(slot)
 *   submit(slot, len, notBefore) ---> tx_q --> nextDue(item)  -> publish -> release(slot)
 *
 * Why a pool of fixed buffers: the worker encodes a tool result straight into
 * the buffer that is later handed to esp-mqtt, so a result is never copied and
 * no task allocates while a request is in flight. `slots x slotSize` bytes are
 * allocated once in begin() (PSRAM when present).
 *
 * Why one consumer: esp-mqtt's API lock is held by its own task across a TCP
 * connect/TLS handshake (up to the network timeout). If the Core-1 tool worker
 * published directly it could stall behind that lock. Here only net_task ever
 * calls into esp-mqtt for publishing (Phase 1 firmware rule 3).
 *
 * MEMORY-SAFETY:
 *  - A slot index is owned by exactly one party at a time: free_q -> producer
 *    (between acquire and submit/release) -> tx_q -> consumer (until release).
 *    Touching buffer(slot) after handing the slot on is a use-after-free.
 *  - submit() checks the frame's own header before queuing it; a slot whose
 *    first 16 bytes are not a valid uplink header is released and counted.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <atomic>

#include "ewp/frame.h"
#include "mcp/os.h"

namespace mcp {

struct TxItem {
    int64_t notBeforeUs;  // BROADCAST reply jitter; 0 = as soon as possible
    uint16_t len;         // header + body bytes in the slot
    uint8_t slot;
    uint8_t channel;  // ewp::Channel
};

class Uplink {
public:
    /** Upper bound on pool slots (compile-time arrays). */
    static constexpr size_t kMaxSlots = 16;

    Uplink() = default;
    /** The device never destroys its uplink; the host tests do (after the consumer stopped). */
    ~Uplink() { os::freePool(pool_); }
    Uplink(const Uplink&) = delete;
    Uplink& operator=(const Uplink&) = delete;

    /** Boot only: allocates slots * slotSize bytes plus two small queues. */
    bool begin(size_t slots, size_t slotSize, bool preferPsram);

    /* ---- producer side (any task) --------------------------------------- */

    /** A free slot index, or -1 when none frees up within timeoutMs (0 = never block). */
    int acquire(uint32_t timeoutMs);
    uint8_t* buffer(int slot) const;
    size_t slotSize() const { return slotSize_; }
    /**
     * Queue a finished frame. On failure the slot is released and the drop is
     * counted - the caller must not touch the slot afterwards either way.
     */
    bool submit(int slot, size_t len, int64_t notBeforeUs, uint32_t timeoutMs);
    /** Give a slot back without sending it. */
    void release(int slot);

    /* ---- consumer side (exactly one task: net_task) ----------------------- */

    /**
     * Next frame that is due. Waits up to maxWaitMs for new frames; frames with
     * notBeforeUs in the future are parked (up to kMaxSlots) and returned once
     * due. Returns false when nothing is due yet - just call again.
     */
    bool nextDue(TxItem& out, uint32_t maxWaitMs);

    /* ---- stats ------------------------------------------------------------ */

    /** Frames waiting for net_task (queued + parked). */
    size_t queued() const { return tx_.waiting() + parkedCount_.load(std::memory_order_relaxed); }
    uint32_t dropped() const { return dropped_.load(std::memory_order_relaxed); }
    void noteDrop() { dropped_.fetch_add(1, std::memory_order_relaxed); }
    size_t freeSlots() const { return free_.waiting(); }
    size_t slots() const { return slots_; }

private:
    bool takeDueParked(TxItem& out, int64_t now);

    uint8_t* pool_ = nullptr;
    size_t slots_ = 0;
    size_t slotSize_ = 0;
    os::TypedQueue<uint8_t> free_;
    os::TypedQueue<TxItem> tx_;
    std::atomic<uint32_t> dropped_{0};

    // Consumer-private parking lot for jittered frames (single consumer: no lock).
    TxItem parked_[kMaxSlots] = {};
    size_t parkedN_ = 0;
    std::atomic<uint32_t> parkedCount_{0};  // mirror of parkedN_ for queued() from other tasks
};

}  // namespace mcp
