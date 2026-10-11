/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | mcp/MCPServer.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The on-device MCP server: a registry of edge tools and the router that moves
 * an EWP/1 request from the network (Core 0) to a tool worker (Core 1) WITHOUT
 * the network side ever waiting for the tool.
 *
 *   Core 0 - MQTT task (esp-mqtt)              Core 1 - mcp_worker
 *   ------------------------------             ------------------------------
 *   onDownlink(frame)                          job_q.receive(..., 1 s)  <- also feeds the TWDT
 *    1-8  checkDownlink(): header only, O(1)    cancelled?  -> ERR CANCELLED
 *    9    tool registered?  else ERR            past deadline? -> ERR DEADLINE_EXCEEDED
 *    10   rid in dedupe window? else ERR        acquire uplink buffer
 *    11   free job slot? (0 ticks) else BUSY    handler(ctx) writes the CBOR result
 *         memcpy frame -> slot (no malloc)        straight into the uplink buffer
 *         ACK (if ACK_REQUESTED)                RES {1: timing, 2: result} / ERR
 *         job_q.send(slot, 0 ticks)  -------->  free the slot, uplink.submit()
 *                                                        |
 *   net_task (Core 0): uplink.nextDue() -> esp_mqtt_client_enqueue()  <-+
 *
 * Every Core-0 operation above has a zero timeout: back-pressure becomes an
 * ERR BUSY frame with retryAfterMs, never a stalled network task (Phase 1
 * firmware rule 2). The job pool (maxInflight x downlinkFrame bytes) and the
 * uplink pool are allocated once in begin() (rule 1).
 *
 * Registering a tool (the real read_telemetry: components/tools/src/EdgeTools.cpp):
 *
 *   static void readTelemetry(mcp::ToolContext& ctx) {
 *       uint8_t sections = kDefaultSections;
 *       if (!parseArgs(ctx, sections)) return;          // ctx.fail(INVALID_ARGS, ..., argKey)
 *       encodeTelemetry(ctx.out(), sections);            // one CBOR map = the result
 *   }
 *   server.registerTool({EWP_TOOL_READ_TELEMETRY, 1,
 *                        EWP_TOOL_FLAG_READ_ONLY | EWP_TOOL_FLAG_IDEMPOTENT,
 *                        EWP_TOOL_NAME_READ_TELEMETRY, &readTelemetry, nullptr});
 *   server.begin(cfg, uplink);   // allocates the pool, starts mcp_worker on Core 1
 *
 * HANDLER RULES (they run on the worker, one at a time, in registration-free order):
 *  - Write exactly ONE CBOR item to ctx.out(), or call ctx.fail() - not both.
 *  - Never block longer than the request deadline; for long work poll
 *    ctx.cancelled()/ctx.expired() and call ctx.keepAlive() at least every
 *    second (Task WDT).
 *  - Never keep pointers into ctx.args() after returning: the job slot is
 *    recycled for the next request immediately.
 *  - Never publish yourself: the result buffer IS the uplink frame.
 *  - Never allocate per call; use static or boot-time buffers.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <atomic>

#include "ewp/cbor.h"
#include "ewp/frame.h"
#include "mcp/Uplink.h"
#include "mcp/os.h"

namespace mcp {

class ToolContext;

/** A tool implementation. Runs on the worker task. */
using ToolHandler = void (*)(ToolContext& ctx);

/** Device-side timing reported in every RES (ExecTiming in tools.ts). */
struct ExecTiming {
    uint32_t queueUs;   // frame receipt (MQTT task) -> worker picked the job up
    uint32_t execUs;    // handler run time on the worker
    int32_t heapDelta;  // internal-RAM free after minus before the handler
};

struct ToolSpec {
    uint8_t id;           // EWP_TOOL_* (header.tool)
    uint8_t version;      // contract version advertised in HELLO.tools
    uint8_t flags;        // EWP_TOOL_FLAG_* bitset
    const char* name;     // LLM-facing name; logs only (the gateway owns names)
    ToolHandler handler;  // never nullptr
    void* user;           // handler state, available as ctx.user()
};

/** What a handler sees. Lives on the worker's stack for the duration of one call. */
class ToolContext {
public:
    uint32_t rid() const { return rid_; }
    uint8_t tool() const { return tool_; }
    uint8_t flags() const { return flags_; }
    void* user() const { return user_; }

    /**
     * The request body (CBOR, normally a map with integer keys). View into the
     * job slot - valid until the handler returns. Empty (len 0) is allowed and
     * means "no arguments".
     */
    const uint8_t* args() const { return args_; }
    size_t argsLen() const { return argsLen_; }

    int64_t deadlineUs() const { return deadlineUs_; }
    int64_t remainingUs() const { return deadlineUs_ - os::nowUs(); }
    bool expired() const { return os::nowUs() >= deadlineUs_; }
    /** The gateway sent CANCEL for this rid while it was running. */
    bool cancelled() const { return cancel_ != nullptr && cancel_->load(std::memory_order_acquire) != 0; }
    /** Feed the task watchdog during long work (inference stages, sensor captures). */
    void keepAlive() const { os::wdtReset(); }

    /** Where the result goes: write exactly one CBOR item. */
    ewp::CborWriter& out() { return out_; }

    /**
     * Report a failure instead of a result. `detail` is COPIED here (up to 96
     * bytes, cut on a UTF-8 boundary), so a handler may pass a buffer on its
     * own stack. It is shown to the LLM as the tool error text. argKey = CBOR
     * key of the bad argument (INVALID_ARGS), -1 for none. retryAfterMs for
     * retryable states (BUSY, NOT_READY, RATE_LIMITED, DEADLINE_EXCEEDED).
     */
    void fail(uint8_t status, const char* detail, int argKey = -1, uint32_t retryAfterMs = 0);
    bool failed() const { return failStatus_ != EWP_ST_OK; }

private:
    friend class MCPServer;
    ToolContext(ewp::CborWriter& out) : out_(out) {}

    ewp::CborWriter& out_;
    const uint8_t* args_ = nullptr;
    size_t argsLen_ = 0;
    const std::atomic<uint32_t>* cancel_ = nullptr;
    void* user_ = nullptr;
    int64_t deadlineUs_ = 0;
    uint32_t rid_ = 0;
    uint32_t failRetryMs_ = 0;
    int failArgKey_ = -1;
    char failDetail_[97] = {};  // 96 bytes + NUL (ErrorBody.detail is Utf8<96>)
    uint8_t tool_ = 0;
    uint8_t flags_ = 0;
    uint8_t failStatus_ = EWP_ST_OK;
};

struct ServerConfig {
    /** Job pool: requests queued + executing (EWP_LIMIT_MAX_INFLIGHT). */
    uint8_t slots = EWP_LIMIT_MAX_INFLIGHT;
    /** Bytes per job slot = largest downlink frame accepted (advertised in HELLO.limits). */
    uint16_t slotSize = EWP_LIMIT_DOWNLINK_FRAME;
    /** Place the job pool in PSRAM when the chip has it. */
    bool preferPsram = true;
    uint32_t workerStackBytes = 6144;
    unsigned workerPriority = 5;
    /** 1 on ESP32-S3 (tools on Core 1); -1 = unpinned (single-core ESP32-C6). */
    int workerCore = 1;
    /** ERR BUSY / DEADLINE_EXCEEDED retry hint. */
    uint32_t busyRetryMs = 250;
    /** BROADCAST requests: replies are spread over 0..jitter ms (no thundering herd). */
    uint32_t broadcastJitterMs = 2000;
    /** How long the worker may wait for a free uplink buffer for a result. */
    uint32_t resultBufferWaitMs = 200;
};

/** Counters for telemetry/logs. Relaxed atomics: approximate is fine, torn is not. */
struct ServerStats {
    std::atomic<uint32_t> received{0};      // downlink frames seen
    std::atomic<uint32_t> dropped{0};       // not EWP / too short / concurrent entry
    std::atomic<uint32_t> rejected{0};      // answered with ERR before queueing
    std::atomic<uint32_t> busy{0};          // ERR BUSY (pool full)
    std::atomic<uint32_t> duplicates{0};    // ERR DUPLICATE
    std::atomic<uint32_t> cancels{0};       // CANCEL frames that matched a job
    std::atomic<uint32_t> expired{0};       // DEADLINE_EXCEEDED before execution
    std::atomic<uint32_t> executed{0};      // handlers run
    std::atomic<uint32_t> failed{0};        // handlers that reported an error
    std::atomic<uint32_t> replyDropped{0};  // ACK/RES/ERR lost (no uplink buffer)
    std::atomic<uint32_t> ticks{0};         // periodic tick callbacks run
};

class MCPServer {
public:
    static constexpr size_t kMaxTools = 8;
    static constexpr size_t kMaxSlots = 8;
    /** REQ rids remembered for duplicate detection (QoS 1 redelivery, gateway retry). */
    static constexpr size_t kDedupeWindow = 16;
    /** ACK + RES/ERR prefix gap reserved before a result: map(1) key(1) timing(<=19) key(1). */
    static constexpr size_t kResultPrefixMax = 22;

    /** Periodic work executed on the worker (telemetry). Must follow the handler rules. */
    using TickHandler = void (*)(void* user);

    MCPServer() = default;
    /** Host tests only: call shutdown() first. The device never destroys its server. */
    ~MCPServer();
    MCPServer(const MCPServer&) = delete;
    MCPServer& operator=(const MCPServer&) = delete;

    /** Before begin() only (the registry is read lock-free afterwards). */
    bool registerTool(const ToolSpec& spec);
    void setTickHandler(TickHandler fn, void* user);

    /**
     * Allocates the job pool, creates the job queue and starts the worker task.
     * `uplink` must already be begun and outlive the server.
     */
    bool begin(const ServerConfig& cfg, Uplink& uplink);
    bool started() const { return started_; }

    /**
     * Transport entry point for one complete downlink MQTT message.
     * Context: the MQTT task (Core 0). NEVER blocks, NEVER allocates.
     * PRECONDITION: called from one task at a time (esp-mqtt delivers messages
     * sequentially; a concurrent entry is detected, dropped and counted).
     * `data` is only read during the call (copied into a job slot).
     */
    void onDownlink(const uint8_t* data, size_t len, ewp::Topic topic);

    /**
     * The transport saw a message bigger than its buffer (esp-mqtt delivers it
     * in fragments). Answers ERR TOO_LARGE using the header in the first
     * fragment, so the gateway gets an error instead of a timeout.
     */
    void onOversized(const uint8_t* firstFragment, size_t fragmentLen, ewp::Topic topic);

    /** Ask the worker to run the tick handler. Any task (esp_timer callback); never blocks. */
    bool postTick();

    /**
     * Stop the worker and wait for it to exit. Host tests only (the device
     * never stops serving); after it returns the object may be destroyed.
     */
    void shutdown();

    /* ---- introspection (HELLO, TELEMETRY, tests) --------------------------- */
    size_t toolCount() const { return toolCount_; }
    const ToolSpec* toolAt(size_t i) const { return i < toolCount_ ? &tools_[i] : nullptr; }
    const ServerStats& stats() const { return stats_; }
    /** Jobs queued or executing right now. */
    uint32_t inFlight() const { return inFlight_.load(std::memory_order_relaxed); }
    size_t slots() const { return slotCount_; }
    size_t slotSize() const { return slotSize_; }
    /** Worst handler/tick execution time since boot (TELEMETRY tasks[].runUsMax). */
    uint32_t workerRunUsMax() const { return workerRunUsMax_.load(std::memory_order_relaxed); }
    void* workerTask() const { return workerTask_; }
    /** Moving average of a tool's execution time in us (0 = no sample yet). */
    uint32_t avgExecUs(uint8_t toolId) const;

private:
    enum SlotState : uint32_t { kFree = 0, kQueued = 1, kRunning = 2 };

    struct Slot {
        uint8_t* frame = nullptr;  // slotSize_ bytes in the job pool
        ewp::Header h;
        int64_t rxUs = 0;
        int64_t deadlineUs = 0;
        int64_t notBeforeUs = 0;   // reply jitter (BROADCAST)
        std::atomic<uint32_t> state{kFree};
        std::atomic<uint32_t> cancelled{0};
        uint16_t len = 0;
        uint8_t toolIndex = 0;
    };

    struct JobMsg {
        uint8_t kind;  // kJob / kTick
        uint8_t slot;
    };
    static constexpr uint8_t kJob = 0;
    static constexpr uint8_t kTick = 1;
    static constexpr uint8_t kStop = 2;

    static void workerEntry(void* self);
    void workerLoop();
    void runJob(uint8_t slotIndex);
    void runTick();

    int findTool(uint8_t id) const;
    bool ridSeen(uint32_t rid) const;
    void rememberRid(uint32_t rid);
    void handleCancel(const ewp::Header& h);
    int64_t replyNotBefore(const ewp::Header& h) const;

    /** Encode and submit an ERR frame. Never blocks when timeoutMs == 0. */
    void sendError(const ewp::Header& req, bool toolTrusted, uint8_t status, const char* detail, int argKey,
                   uint32_t retryAfterMs, int64_t notBeforeUs, uint32_t timeoutMs);
    void sendAck(const ewp::Header& req, uint32_t ahead, uint32_t etaMs, int64_t notBeforeUs);
    void freeSlot(uint8_t slotIndex);

    ToolSpec tools_[kMaxTools] = {};
    std::atomic<uint32_t> avgExecUs_[kMaxTools] = {};
    size_t toolCount_ = 0;

    Slot slots_[kMaxSlots];
    size_t slotCount_ = 0;
    size_t slotSize_ = 0;
    os::TypedQueue<uint8_t> freeSlots_;
    os::TypedQueue<JobMsg> jobs_;
    std::atomic<uint32_t> inFlight_{0};
    std::atomic<uint32_t> running_{0};
    std::atomic<uint32_t> tickQueued_{0};
    std::atomic<uint32_t> inDownlink_{0};

    // Dedupe ring: touched only by onDownlink() (single transport task).
    uint32_t recentRids_[kDedupeWindow] = {};
    size_t recentHead_ = 0;
    size_t recentCount_ = 0;

    TickHandler tickFn_ = nullptr;
    void* tickUser_ = nullptr;

    ServerConfig cfg_;
    uint8_t* pool_ = nullptr;
    Uplink* uplink_ = nullptr;
    void* workerTask_ = nullptr;
    std::atomic<uint32_t> workerRunUsMax_{0};
    std::atomic<uint32_t> workerExited_{0};
    ServerStats stats_;
    bool started_ = false;
};

}  // namespace mcp
