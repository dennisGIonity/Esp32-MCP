/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | mcp/MCPServer.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Routing and execution. Read MCPServer.h first; the comments here explain
 * the ordering decisions that the header only states.
 */
#include "mcp/MCPServer.h"

#include <string.h>

#ifdef ESP_PLATFORM
#include "esp_log.h"
#define MCP_LOGW(...) ESP_LOGW("mcp", __VA_ARGS__)
#define MCP_LOGI(...) ESP_LOGI("mcp", __VA_ARGS__)
#else
#define MCP_LOGW(...) ((void)0)
#define MCP_LOGI(...) ((void)0)
#endif

namespace mcp {
namespace {

constexpr size_t kDetailMax = 96;  // ErrorBody.detail is Utf8<96>

/** Clamp a non-negative microsecond span into u32 (a 71-minute span saturates). */
uint32_t sat32(int64_t us) {
    if (us <= 0) return 0;
    return us > static_cast<int64_t>(UINT32_MAX) ? UINT32_MAX : static_cast<uint32_t>(us);
}

/** Longest prefix of `s` (<= max bytes) that does not cut a UTF-8 sequence. */
size_t utf8Prefix(const char* s, size_t max) {
    const size_t n = strnlen(s, max + 1);
    if (n <= max) return n;
    size_t cut = max;
    while (cut > 0 && (static_cast<uint8_t>(s[cut]) & 0xC0u) == 0x80u) --cut;  // step back over continuations
    return cut;
}

void writeTiming(ewp::CborWriter& w, const ExecTiming& t) {
    ewp::CborMap m(w);
    m.key(EWP_K_EXEC_TIMING_QUEUE_US).uint(t.queueUs);
    m.key(EWP_K_EXEC_TIMING_EXEC_US).uint(t.execUs);
    m.key(EWP_K_EXEC_TIMING_HEAP_DELTA).sint(t.heapDelta);
}

/**
 * ERR frame into `buf`. Returns the frame length, 0 if it did not fit.
 * `detail` must be valid UTF-8 (it is cut on a code-point boundary here).
 */
size_t encodeError(uint8_t* buf, size_t cap, const ewp::Header& req, bool toolTrusted, uint8_t status,
                   const char* detail, int argKey, uint32_t retryAfterMs, const ExecTiming* timing) {
    if (cap < EWP_HEADER_LEN + 1) return 0;
    ewp::CborWriter w(buf + EWP_HEADER_LEN, cap - EWP_HEADER_LEN);
    {
        ewp::CborMap m(w);
        if (detail != nullptr && detail[0] != '\0') {
            const size_t n = utf8Prefix(detail, kDetailMax);
            if (ewp::utf8_valid(detail, n)) {
                m.key(EWP_K_ERROR_BODY_DETAIL).text(detail, n);
            } else {
                m.key(EWP_K_ERROR_BODY_DETAIL).text("tool error (detail was not valid UTF-8)");
            }
        }
        if (argKey >= 0) m.key(EWP_K_ERROR_BODY_ARG_KEY).uint(static_cast<uint64_t>(argKey));
        if (retryAfterMs != 0) m.key(EWP_K_ERROR_BODY_RETRY_AFTER_MS).uint(retryAfterMs);
        if (timing != nullptr) writeTiming(m.key(EWP_K_ERROR_BODY_TIMING), *timing);
    }
    if (!w.ok()) return 0;
    ewp::Header h;
    h.type = EWP_MSG_ERR;
    h.rid = req.rid;
    // "tool = NO_TOOL when the frame was rejected before the tool id could be trusted"
    h.tool = toolTrusted ? req.tool : static_cast<uint8_t>(EWP_NO_TOOL);
    h.status = status;
    h.bodyLen = static_cast<uint16_t>(w.size());
    ewp::encodeHeader(h, buf);
    return EWP_HEADER_LEN + w.size();
}

}  // namespace

/* ------------------------------------------------------------------------- */
/* ToolContext                                                                */
/* ------------------------------------------------------------------------- */

void ToolContext::fail(uint8_t status, const char* detail, int argKey, uint32_t retryAfterMs) {
    // OK / ACCEPTED are not failures: map a careless call to INTERNAL rather than sending "OK" as an error.
    failStatus_ = (status == EWP_ST_OK || status == EWP_ST_ACCEPTED) ? static_cast<uint8_t>(EWP_ST_INTERNAL) : status;
    failArgKey_ = argKey;
    failRetryMs_ = retryAfterMs;
    // MEMORY-SAFETY: copy now - `detail` may live on the handler's stack, which is gone after it returns.
    const char* d = detail != nullptr ? detail : "";
    const size_t n = utf8Prefix(d, kDetailMax);
    memcpy(failDetail_, d, n);
    failDetail_[n] = '\0';
}

/* ------------------------------------------------------------------------- */
/* Setup                                                                      */
/* ------------------------------------------------------------------------- */

bool MCPServer::registerTool(const ToolSpec& spec) {
    if (started_ || spec.handler == nullptr || spec.id == EWP_NO_TOOL || toolCount_ >= kMaxTools) return false;
    if (findTool(spec.id) >= 0) return false;  // ids are unique
    tools_[toolCount_++] = spec;
    return true;
}

void MCPServer::setTickHandler(TickHandler fn, void* user) {
    if (started_) return;
    tickFn_ = fn;
    tickUser_ = user;
}

bool MCPServer::begin(const ServerConfig& cfg, Uplink& uplink) {
    if (started_ || cfg.slots == 0 || cfg.slots > kMaxSlots || cfg.slotSize < EWP_HEADER_LEN ||
        cfg.slotSize > EWP_LIMIT_DOWNLINK_FRAME) {
        return false;
    }
    // Results are encoded after a reserved prefix gap: the uplink buffer must hold header + gap + something.
    if (uplink.slotSize() < EWP_HEADER_LEN + kResultPrefixMax + 64) return false;
    cfg_ = cfg;
    uplink_ = &uplink;

    // MEMORY: the only allocation of the request path, once, at boot.
    uint8_t* pool = static_cast<uint8_t*>(os::allocPool(static_cast<size_t>(cfg.slots) * cfg.slotSize, cfg.preferPsram));
    if (pool == nullptr) return false;
    pool_ = pool;
    for (size_t i = 0; i < cfg.slots; ++i) slots_[i].frame = pool + i * cfg.slotSize;
    slotCount_ = cfg.slots;
    slotSize_ = cfg.slotSize;

    // job_q holds every slot + one tick + one stop message, so a send of a
    // claimed slot can never find it full (see onDownlink).
    if (!freeSlots_.begin(slotCount_) || !jobs_.begin(slotCount_ + 2)) return false;
    for (size_t i = 0; i < slotCount_; ++i) {
        const uint8_t idx = static_cast<uint8_t>(i);
        freeSlots_.send(idx, 0);
    }

    started_ = true;  // the worker and onDownlink() read the configuration from here on
    if (!os::spawn("mcp_worker", &MCPServer::workerEntry, this, cfg.workerStackBytes, cfg.workerPriority,
                   cfg.workerCore, &workerTask_)) {
        started_ = false;
        return false;
    }
    return true;
}

MCPServer::~MCPServer() {
    if (!started_) os::freePool(pool_);  // a running worker would still use it: leak rather than crash
}

void MCPServer::shutdown() {
    if (!started_) return;
    const JobMsg m{kStop, 0};
    jobs_.send(m, os::kForever);
    while (workerExited_.load(std::memory_order_acquire) == 0) os::delayMs(1);
    started_ = false;
}

/* ------------------------------------------------------------------------- */
/* Core 0: the transport entry point                                          */
/* ------------------------------------------------------------------------- */

int MCPServer::findTool(uint8_t id) const {
    for (size_t i = 0; i < toolCount_; ++i) {
        if (tools_[i].id == id) return static_cast<int>(i);
    }
    return -1;
}

bool MCPServer::ridSeen(uint32_t rid) const {
    for (size_t i = 0; i < recentCount_; ++i) {
        if (recentRids_[i] == rid) return true;
    }
    return false;
}

void MCPServer::rememberRid(uint32_t rid) {
    recentRids_[recentHead_] = rid;
    recentHead_ = (recentHead_ + 1) % kDedupeWindow;
    if (recentCount_ < kDedupeWindow) ++recentCount_;
}

int64_t MCPServer::replyNotBefore(const ewp::Header& h) const {
    if ((h.flags & EWP_FLAG_BROADCAST) == 0 || cfg_.broadcastJitterMs == 0) return 0;
    const uint64_t spanUs = static_cast<uint64_t>(cfg_.broadcastJitterMs) * 1000u;
    return os::nowUs() + static_cast<int64_t>(os::random32() % spanUs);
}

uint32_t MCPServer::avgExecUs(uint8_t toolId) const {
    const int i = findTool(toolId);
    return i < 0 ? 0 : avgExecUs_[i].load(std::memory_order_relaxed);
}

void MCPServer::sendError(const ewp::Header& req, bool toolTrusted, uint8_t status, const char* detail, int argKey,
                          uint32_t retryAfterMs, int64_t notBeforeUs, uint32_t timeoutMs) {
    const int up = uplink_->acquire(timeoutMs);
    if (up < 0) {
        stats_.replyDropped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    const size_t len = encodeError(uplink_->buffer(up), uplink_->slotSize(), req, toolTrusted, status, detail, argKey,
                                   retryAfterMs, nullptr);
    if (len == 0 || !uplink_->submit(up, len, notBeforeUs, timeoutMs)) {
        if (len == 0) uplink_->release(up);
        stats_.replyDropped.fetch_add(1, std::memory_order_relaxed);
    }
}

void MCPServer::sendAck(const ewp::Header& req, uint32_t ahead, uint32_t etaMs, int64_t notBeforeUs) {
    const int up = uplink_->acquire(0);  // Core 0: never wait; an ACK is advisory
    if (up < 0) {
        stats_.replyDropped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    uint8_t* buf = uplink_->buffer(up);
    ewp::CborWriter w(buf + EWP_HEADER_LEN, uplink_->slotSize() - EWP_HEADER_LEN);
    {
        ewp::CborMap m(w);
        m.key(EWP_K_ACK_BODY_QUEUE_DEPTH).uint(ahead > 255 ? 255 : ahead);
        if (etaMs != 0) m.key(EWP_K_ACK_BODY_ETA_MS).uint(etaMs);
    }
    ewp::Header h;
    h.type = EWP_MSG_ACK;
    h.rid = req.rid;
    h.tool = req.tool;
    h.status = EWP_ST_ACCEPTED;
    h.bodyLen = static_cast<uint16_t>(w.size());
    ewp::encodeHeader(h, buf);
    if (!w.ok() || !uplink_->submit(up, EWP_HEADER_LEN + w.size(), notBeforeUs, 0)) {
        if (!w.ok()) uplink_->release(up);
        stats_.replyDropped.fetch_add(1, std::memory_order_relaxed);
    }
}

void MCPServer::handleCancel(const ewp::Header& h) {
    // Same task as the claim in onDownlink(), so a slot cannot be re-claimed
    // between the rid comparison and the flag store below. A worker freeing
    // the slot meanwhile is harmless: the flag is reset at the next claim.
    for (size_t i = 0; i < slotCount_; ++i) {
        Slot& s = slots_[i];
        const uint32_t st = s.state.load(std::memory_order_acquire);
        if ((st == kQueued || st == kRunning) && s.h.rid == h.rid) {
            s.cancelled.store(1, std::memory_order_release);
            stats_.cancels.fetch_add(1, std::memory_order_relaxed);
            MCP_LOGI("CANCEL rid=%08x (%s)", static_cast<unsigned>(h.rid), st == kQueued ? "queued" : "running");
            return;
        }
    }
    // Unknown rid: already answered or never seen. CANCEL is not answered.
}

void MCPServer::onDownlink(const uint8_t* data, size_t len, ewp::Topic topic) {
    stats_.received.fetch_add(1, std::memory_order_relaxed);
    if (!started_) {
        stats_.dropped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    // Precondition check (single transport task). Detect, never corrupt.
    if (inDownlink_.exchange(1, std::memory_order_acquire) != 0) {
        stats_.dropped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    struct Leave {
        std::atomic<uint32_t>& flag;
        ~Leave() { flag.store(0, std::memory_order_release); }
    } leave{inDownlink_};

    // Steps 1-8: header only, constant time.
    const ewp::DownlinkCheck c = ewp::checkDownlink(data, len, topic, slotSize_);
    if (c.verdict == ewp::Verdict::Drop) {
        stats_.dropped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    const int64_t notBefore = replyNotBefore(c.h);
    if (c.verdict == ewp::Verdict::Reject) {
        stats_.rejected.fetch_add(1, std::memory_order_relaxed);
        MCP_LOGW("rejected rid=%08x: %s", static_cast<unsigned>(c.h.rid), c.detail);
        sendError(c.h, false, c.status, c.detail, -1, 0, notBefore, 0);
        return;
    }
    if (c.h.type == EWP_MSG_CANCEL) {
        handleCancel(c.h);
        return;
    }

    // Step 9: tool registered.
    const int toolIndex = findTool(c.h.tool);
    if (toolIndex < 0) {
        stats_.rejected.fetch_add(1, std::memory_order_relaxed);
        sendError(c.h, true, EWP_ST_UNKNOWN_TOOL, "tool not available on this device", -1, 0, notBefore, 0);
        return;
    }
    // Step 10: rid dedupe (QoS 1 redelivery or a gateway retry of the same rid).
    if (ridSeen(c.h.rid)) {
        stats_.duplicates.fetch_add(1, std::memory_order_relaxed);
        sendError(c.h, true, EWP_ST_DUPLICATE, "duplicate request id", -1, 0, notBefore, 0);
        return;
    }
    // Step 11: a free job slot - ZERO ticks. Back-pressure is an error frame.
    uint8_t idx;
    if (!freeSlots_.receive(idx, 0)) {
        stats_.busy.fetch_add(1, std::memory_order_relaxed);
        sendError(c.h, true, EWP_ST_BUSY, "device busy, retry later", -1, cfg_.busyRetryMs, notBefore, 0);
        return;
    }

    // Copy the frame into the pool. MEMORY-SAFETY: len <= slotSize_ was
    // checked by checkDownlink(); `data` belongs to esp-mqtt and is reused
    // after we return, so the job must never point into it.
    Slot& s = slots_[idx];
    memcpy(s.frame, data, len);
    const int64_t now = os::nowUs();
    const uint32_t budgetMs = c.h.deadlineMs != 0 ? c.h.deadlineMs : EWP_LIMIT_DEFAULT_DEADLINE_MS;
    s.len = static_cast<uint16_t>(len);
    s.h = c.h;
    s.rxUs = now;
    s.deadlineUs = now + static_cast<int64_t>(budgetMs) * 1000;
    s.notBeforeUs = notBefore;
    s.toolIndex = static_cast<uint8_t>(toolIndex);
    s.cancelled.store(0, std::memory_order_relaxed);
    s.state.store(kQueued, std::memory_order_release);
    rememberRid(c.h.rid);

    const bool priority = (c.h.flags & EWP_FLAG_PRIORITY) != 0;
    const uint32_t ahead = priority ? running_.load(std::memory_order_relaxed) : inFlight_.load(std::memory_order_relaxed);
    inFlight_.fetch_add(1, std::memory_order_relaxed);

    // ACK goes into tx_q BEFORE the job is visible to the worker, so the
    // gateway always sees ACK before RES for one rid (same FIFO).
    if ((c.h.flags & EWP_FLAG_ACK_REQUESTED) != 0) {
        // eta from the tool's moving average; absent (0) until the tool has run once.
        const uint64_t avg = avgExecUs_[toolIndex].load(std::memory_order_relaxed);
        const uint64_t eta = avg == 0 ? 0u : ((ahead + 1u) * avg + 999u) / 1000u;
        sendAck(c.h, ahead, eta > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(eta), notBefore);
    }

    const JobMsg m{kJob, idx};
    const bool queued = priority ? jobs_.sendToFront(m, 0) : jobs_.send(m, 0);
    if (!queued) {
        // Unreachable by construction (job_q capacity = slots + 2). Never leak the slot.
        s.state.store(kFree, std::memory_order_release);
        inFlight_.fetch_sub(1, std::memory_order_relaxed);
        freeSlots_.send(idx, 0);
        stats_.busy.fetch_add(1, std::memory_order_relaxed);
        sendError(c.h, true, EWP_ST_BUSY, "device busy, retry later", -1, cfg_.busyRetryMs, notBefore, 0);
    }
}

void MCPServer::onOversized(const uint8_t* first, size_t n, ewp::Topic) {
    stats_.received.fetch_add(1, std::memory_order_relaxed);
    ewp::RawHeader raw;
    if (!started_ || !ewp::decodeHeader(first, n, raw) || raw.magic != EWP_MAGIC) {
        stats_.dropped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (inDownlink_.exchange(1, std::memory_order_acquire) != 0) {
        stats_.dropped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    stats_.rejected.fetch_add(1, std::memory_order_relaxed);
    sendError(raw.h, false, EWP_ST_TOO_LARGE, "frame larger than the device downlink limit", -1, 0,
              replyNotBefore(raw.h), 0);
    inDownlink_.store(0, std::memory_order_release);
}

bool MCPServer::postTick() {
    if (!started_) return false;
    uint32_t expected = 0;
    if (!tickQueued_.compare_exchange_strong(expected, 1, std::memory_order_acq_rel)) return false;  // one pending is enough
    const JobMsg m{kTick, 0};
    if (!jobs_.send(m, 0)) {
        tickQueued_.store(0, std::memory_order_release);
        return false;
    }
    return true;
}

/* ------------------------------------------------------------------------- */
/* Worker (Core 1 on ESP32-S3)                                                 */
/* ------------------------------------------------------------------------- */

void MCPServer::workerEntry(void* self) {
    static_cast<MCPServer*>(self)->workerLoop();
    os::exitTask();
}

void MCPServer::workerLoop() {
    os::wdtSubscribe();
    for (;;) {
        JobMsg m;
        // 1 s timeout: the loop feeds the Task WDT even when idle.
        const bool got = jobs_.receive(m, 1000);
        os::wdtReset();
        if (!got) continue;
        if (m.kind == kStop) break;
        const int64_t t0 = os::nowUs();
        if (m.kind == kTick) {
            tickQueued_.store(0, std::memory_order_release);
            runTick();
        } else if (m.slot < slotCount_) {
            runJob(m.slot);
        }
        const uint32_t dt = sat32(os::nowUs() - t0);
        if (dt > workerRunUsMax_.load(std::memory_order_relaxed)) workerRunUsMax_.store(dt, std::memory_order_relaxed);
    }
    os::wdtUnsubscribe();
    workerExited_.store(1, std::memory_order_release);
}

void MCPServer::runTick() {
    stats_.ticks.fetch_add(1, std::memory_order_relaxed);
    if (tickFn_ != nullptr) tickFn_(tickUser_);
}

void MCPServer::freeSlot(uint8_t slotIndex) {
    slots_[slotIndex].state.store(kFree, std::memory_order_release);
    inFlight_.fetch_sub(1, std::memory_order_relaxed);
    freeSlots_.send(slotIndex, 0);  // capacity == slots and the index is owned once: cannot fail
}

void MCPServer::runJob(uint8_t slotIndex) {
    Slot& s = slots_[slotIndex];
    s.state.store(kRunning, std::memory_order_release);
    running_.store(1, std::memory_order_relaxed);
    const ToolSpec& spec = tools_[s.toolIndex];
    const ewp::Header req = s.h;  // copy: the slot is recycled before the reply is submitted
    const int64_t nb = s.notBeforeUs;
    const int64_t start = os::nowUs();
    const uint32_t queueUs = sat32(start - s.rxUs);

    // Cancelled or expired while queued: answer, never execute (no stale actuation).
    if (s.cancelled.load(std::memory_order_acquire) != 0) {
        freeSlot(slotIndex);
        running_.store(0, std::memory_order_relaxed);
        sendError(req, true, EWP_ST_CANCELLED, "cancelled before execution", -1, 0, nb, cfg_.resultBufferWaitMs);
        return;
    }
    if (start >= s.deadlineUs) {
        stats_.expired.fetch_add(1, std::memory_order_relaxed);
        freeSlot(slotIndex);
        running_.store(0, std::memory_order_relaxed);
        sendError(req, true, EWP_ST_DEADLINE_EXCEEDED, "deadline passed while queued", -1, cfg_.busyRetryMs, nb,
                  cfg_.resultBufferWaitMs);
        return;
    }

    // The result is written straight into an uplink buffer (no copy, no malloc).
    const int64_t leftMs = (s.deadlineUs - start) / 1000;
    const uint32_t waitMs = leftMs < static_cast<int64_t>(cfg_.resultBufferWaitMs) ? static_cast<uint32_t>(leftMs)
                                                                                  : cfg_.resultBufferWaitMs;
    const int up = uplink_->acquire(waitMs);
    if (up < 0) {
        stats_.replyDropped.fetch_add(1, std::memory_order_relaxed);
        freeSlot(slotIndex);
        running_.store(0, std::memory_order_relaxed);
        MCP_LOGW("rid=%08x dropped: no uplink buffer", static_cast<unsigned>(req.rid));
        return;
    }
    uint8_t* buf = uplink_->buffer(up);
    const size_t cap = uplink_->slotSize();
    uint8_t* body = buf + EWP_HEADER_LEN;
    // [ header 16 | gap kResultPrefixMax | result ... ]  -> gap closed below
    ewp::CborWriter out(body + kResultPrefixMax, cap - EWP_HEADER_LEN - kResultPrefixMax);

    ToolContext ctx(out);
    ctx.args_ = s.frame + EWP_HEADER_LEN;
    ctx.argsLen_ = req.bodyLen;
    ctx.cancel_ = &s.cancelled;
    ctx.user_ = spec.user;
    ctx.deadlineUs_ = s.deadlineUs;
    ctx.rid_ = req.rid;
    ctx.tool_ = req.tool;
    ctx.flags_ = req.flags;

    const uint32_t heapBefore = os::internalFreeBytes();
    spec.handler(ctx);
    const int64_t end = os::nowUs();
    const int64_t heapDiff = static_cast<int64_t>(os::internalFreeBytes()) - static_cast<int64_t>(heapBefore);
    const ExecTiming timing{queueUs, sat32(end - start), static_cast<int32_t>(heapDiff)};
    stats_.executed.fetch_add(1, std::memory_order_relaxed);
    {
        // Exponential moving average (1/8) of the execution time for ACK eta.
        const uint64_t prev = avgExecUs_[s.toolIndex].load(std::memory_order_relaxed);
        const uint64_t next = prev == 0 ? timing.execUs : (prev * 7u + timing.execUs) / 8u;
        avgExecUs_[s.toolIndex].store(static_cast<uint32_t>(next > UINT32_MAX ? UINT32_MAX : next),
                                      std::memory_order_relaxed);
    }

    // The handler must leave exactly one well-formed CBOR item.
    if (!ctx.failed()) {
        bool valid = out.ok() && out.size() > 0;
        if (valid) {
            ewp::CborReader check(out.data(), out.size());
            valid = check.skip() && check.atEnd();
        }
        if (!valid) {
            ctx.fail(EWP_ST_INTERNAL, out.ok() ? "tool produced an invalid result" : "result larger than the uplink buffer");
        }
    }

    size_t frameLen;
    if (ctx.failed()) {
        stats_.failed.fetch_add(1, std::memory_order_relaxed);
        frameLen = encodeError(buf, cap, req, true, ctx.failStatus_, ctx.failDetail_, ctx.failArgKey_,
                               ctx.failRetryMs_, &timing);
    } else {
        // RES body = {1: timing, 2: result}. The timing is known only now, so
        // it is encoded into a small prefix and the result is moved left to
        // close the reserved gap. MEMORY-SAFETY: memmove (regions overlap).
        uint8_t prefix[kResultPrefixMax];
        ewp::CborWriter pw(prefix, sizeof prefix);
        pw.map(2);
        pw.uint(EWP_K_RESULT_BODY_TIMING);
        writeTiming(pw, timing);
        pw.uint(EWP_K_RESULT_BODY_RESULT);
        // Worst case 1 + 1 + 19 + 1 = 22 bytes == kResultPrefixMax; ok() proves it.
        if (!pw.ok()) {
            frameLen = encodeError(buf, cap, req, true, EWP_ST_INTERNAL, "timing prefix overflow", -1, 0, &timing);
        } else {
            const size_t resultLen = out.size();
            memmove(body + pw.size(), body + kResultPrefixMax, resultLen);
            memcpy(body, prefix, pw.size());
            ewp::Header h;
            h.type = EWP_MSG_RES;
            h.rid = req.rid;
            h.tool = req.tool;
            h.status = EWP_ST_OK;
            h.bodyLen = static_cast<uint16_t>(pw.size() + resultLen);
            ewp::encodeHeader(h, buf);
            frameLen = EWP_HEADER_LEN + h.bodyLen;
        }
    }

    // Args are no longer referenced (the ERR detail was copied into ctx): recycle the job slot first,
    // so a waiting request can be accepted while the reply queues.
    freeSlot(slotIndex);
    running_.store(0, std::memory_order_relaxed);

    if (frameLen == 0) {
        uplink_->release(up);
        stats_.replyDropped.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    if (!uplink_->submit(up, frameLen, nb, cfg_.resultBufferWaitMs)) {
        stats_.replyDropped.fetch_add(1, std::memory_order_relaxed);
    }
}

}  // namespace mcp
