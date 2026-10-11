/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/test_mcp.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * MCPServer routing on real threads: the test thread plays the MQTT task
 * (onDownlink) and the consumer (uplink.nextDue), the server runs its own
 * worker thread exactly as on the device.
 */
#include <stdio.h>
#include <string.h>

#include <atomic>
#include <memory>
#include <thread>

#include "check.h"
#include "frames.h"
#include "mcp/MCPServer.h"

using namespace tf;

namespace {

constexpr uint8_t kEcho = 0x41;    // result {1: argsLen}
constexpr uint8_t kGate = 0x42;    // waits for gOpen, honours CANCEL
constexpr uint8_t kFail = 0x43;    // INVALID_ARGS with a stack-buffer detail
constexpr uint8_t kBad = 0x44;     // two CBOR items (invalid result)
constexpr uint8_t kBig = 0x45;     // result larger than the uplink buffer
constexpr uint8_t kNone = 0x46;    // writes nothing

std::atomic<int> gOpen{0};
std::atomic<int> gGateRuns{0};
std::atomic<int> gEchoRuns{0};

void echoTool(mcp::ToolContext& ctx) {
    gEchoRuns.fetch_add(1);
    ewp::CborMap m(ctx.out());
    m.key(1).uint(ctx.argsLen());
}

void gateTool(mcp::ToolContext& ctx) {
    gGateRuns.fetch_add(1);
    while (gOpen.load() == 0) {
        if (ctx.cancelled()) {
            ctx.fail(EWP_ST_CANCELLED, "cancelled while running");
            return;
        }
        ctx.keepAlive();
        os::delayMs(1);
    }
    ctx.out().map(0);
}

void failTool(mcp::ToolContext& ctx) {
    char detail[64];  // on the handler's stack: the server must copy it
    snprintf(detail, sizeof detail, "channel %u out of range", 9u);
    ctx.fail(EWP_ST_INVALID_ARGS, detail, 7);
}

void badTool(mcp::ToolContext& ctx) {
    ctx.out().uint(1);
    ctx.out().uint(2);
}

void bigTool(mcp::ToolContext& ctx) {
    static uint8_t junk[4096];
    ctx.out().bytes(junk, sizeof junk);
}

void noneTool(mcp::ToolContext&) {}

struct Rig {
    mcp::Uplink up;
    mcp::MCPServer srv;
    explicit Rig(uint8_t slots = 4, uint32_t jitterMs = 0, uint8_t upSlots = 8) {
        gOpen.store(0);
        gGateRuns.store(0);
        gEchoRuns.store(0);
        CHECK(up.begin(upSlots, 2048, false));
        CHECK(srv.registerTool({kEcho, 1, EWP_TOOL_FLAG_READ_ONLY, "echo", &echoTool, nullptr}));
        CHECK(srv.registerTool({kGate, 1, 0, "gate", &gateTool, nullptr}));
        CHECK(srv.registerTool({kFail, 1, 0, "fail", &failTool, nullptr}));
        CHECK(srv.registerTool({kBad, 1, 0, "bad", &badTool, nullptr}));
        CHECK(srv.registerTool({kBig, 1, 0, "big", &bigTool, nullptr}));
        CHECK(srv.registerTool({kNone, 1, 0, "none", &noneTool, nullptr}));
        mcp::ServerConfig cfg;
        cfg.slots = slots;
        cfg.workerCore = -1;
        cfg.preferPsram = false;
        cfg.broadcastJitterMs = jitterMs;
        CHECK(srv.begin(cfg, up));
    }
    ~Rig() {
        gOpen.store(1);
        srv.shutdown();
    }
    void send(const Bytes& f, ewp::Topic t = ewp::Topic::Unicast) { srv.onDownlink(f.data(), f.size(), t); }
};

/** Wait until the gate tool is executing (the worker is busy). */
void waitGateRunning(int runs = 1) {
    for (int i = 0; i < 2000 && gGateRuns.load() < runs; ++i) os::delayMs(1);
}

}  // namespace

TEST(mcp_registry_rules) {
    mcp::MCPServer s;
    CHECK(s.registerTool({1, 1, 0, "a", &echoTool, nullptr}));
    CHECK(!s.registerTool({1, 1, 0, "dup", &echoTool, nullptr}));  // duplicate id
    CHECK(!s.registerTool({0, 1, 0, "zero", &echoTool, nullptr}));  // NO_TOOL
    CHECK(!s.registerTool({2, 1, 0, "null", nullptr, nullptr}));    // no handler
    CHECK_EQ(s.toolCount(), 1u);
}

TEST(mcp_request_gets_ack_then_res_with_timing) {
    Rig rig;
    rig.send(req(0x0100002A, kEcho, EWP_FLAG_ACK_REQUESTED, 3000, {0xA1, 0x01, 0x03}));
    Out ack, res;
    CHECK(pop(rig.up, ack));
    CHECK(pop(rig.up, res));
    CHECK_EQ(ack.h.type, EWP_MSG_ACK);
    CHECK_EQ(ack.h.status, EWP_ST_ACCEPTED);
    CHECK_EQ(ack.h.rid, 0x0100002Au);
    CHECK_EQ(ack.h.tool, kEcho);
    uint64_t depth = 99;
    CHECK(getUint(ack.body(), ack.bodyLen(), EWP_K_ACK_BODY_QUEUE_DEPTH, depth));
    CHECK_EQ(depth, 0u);
    CHECK(!hasKey(ack.body(), ack.bodyLen(), EWP_K_ACK_BODY_ETA_MS));  // no execution sample yet

    CHECK_EQ(res.h.type, EWP_MSG_RES);
    CHECK_EQ(res.h.status, EWP_ST_OK);
    CHECK_EQ(res.h.rid, 0x0100002Au);
    CHECK_EQ(res.h.tool, kEcho);
    CHECK_EQ(res.channel, static_cast<uint8_t>(ewp::Channel::Result));
    CHECK(wellFormed(res.body(), res.bodyLen()));
    // canonical {1: timing, 2: result}: key 1 first
    CHECK_EQ(res.body()[0], 0xA2);
    CHECK_EQ(res.body()[1], 0x01);
    const uint8_t* t = nullptr;
    size_t tn = 0;
    CHECK(getRaw(res.body(), res.bodyLen(), EWP_K_RESULT_BODY_TIMING, t, tn));
    uint64_t q = 0, e = 0;
    CHECK(getUint(t, tn, EWP_K_EXEC_TIMING_QUEUE_US, q));
    CHECK(getUint(t, tn, EWP_K_EXEC_TIMING_EXEC_US, e));
    int64_t hd = 1;
    CHECK(getInt(t, tn, EWP_K_EXEC_TIMING_HEAP_DELTA, hd));
    CHECK_EQ(hd, 0);  // host: internalFreeBytes() == 0
    const uint8_t* r = nullptr;
    size_t rn = 0;
    CHECK(getRaw(res.body(), res.bodyLen(), EWP_K_RESULT_BODY_RESULT, r, rn));
    uint64_t argsLen = 0;
    CHECK(getUint(r, rn, 1, argsLen));
    CHECK_EQ(argsLen, 3u);
    CHECK_EQ(rig.srv.stats().executed.load(), 1u);
}

TEST(mcp_ack_eta_after_first_run) {
    Rig rig;
    rig.send(req(1, kEcho));
    Out o;
    CHECK(pop(rig.up, o));
    rig.send(req(2, kEcho, EWP_FLAG_ACK_REQUESTED));
    Out ack;
    CHECK(pop(rig.up, ack));
    CHECK_EQ(ack.h.type, EWP_MSG_ACK);
    uint64_t eta = 0;
    CHECK(getUint(ack.body(), ack.bodyLen(), EWP_K_ACK_BODY_ETA_MS, eta));
    CHECK(eta >= 1);
    CHECK(pop(rig.up, o));
}

TEST(mcp_unknown_tool_and_header_errors) {
    Rig rig;
    rig.send(req(5, 0x7E));
    Out o;
    CHECK(pop(rig.up, o));
    CHECK_EQ(o.h.type, EWP_MSG_ERR);
    CHECK_EQ(o.h.status, EWP_ST_UNKNOWN_TOOL);
    CHECK_EQ(o.h.tool, 0x7E);  // trusted: echoed
    CHECK_EQ(o.h.rid, 5u);

    Bytes v2 = req(6, kEcho);
    v2[1] = 2;  // version 2
    rig.send(v2);
    CHECK(pop(rig.up, o));
    CHECK_EQ(o.h.status, EWP_ST_UNSUPPORTED_VERSION);
    CHECK_EQ(o.h.tool, EWP_NO_TOOL);  // rejected before the tool id could be trusted
    std::string detail;
    CHECK(getText(o.body(), o.bodyLen(), EWP_K_ERROR_BODY_DETAIL, detail));
    CHECK(!detail.empty());

    Bytes junk = {'{', '"', 'a', '"', ':', '1', '}', 0, 0, 0, 0, 0, 0, 0, 0, 0};
    rig.send(junk);  // legacy JSON: dropped, not answered
    CHECK(!pop(rig.up, o, 50));
    CHECK_EQ(rig.srv.stats().dropped.load(), 1u);
}

TEST(mcp_duplicate_rid_is_rejected) {
    Rig rig;
    rig.send(req(77, kEcho));
    Out o;
    CHECK(pop(rig.up, o));
    CHECK_EQ(o.h.type, EWP_MSG_RES);
    rig.send(req(77, kEcho));
    CHECK(pop(rig.up, o));
    CHECK_EQ(o.h.type, EWP_MSG_ERR);
    CHECK_EQ(o.h.status, EWP_ST_DUPLICATE);
    CHECK_EQ(gEchoRuns.load(), 1);
}

TEST(mcp_busy_when_pool_full_and_recovers) {
    Rig rig(4);
    rig.send(req(1, kGate));
    waitGateRunning();
    for (uint32_t rid = 2; rid <= 4; ++rid) rig.send(req(rid, kEcho));  // 1 running + 3 queued = 4 slots
    rig.send(req(5, kEcho));  // pool full
    Out o;
    CHECK(pop(rig.up, o));
    CHECK_EQ(o.h.type, EWP_MSG_ERR);
    CHECK_EQ(o.h.status, EWP_ST_BUSY);
    CHECK_EQ(o.h.rid, 5u);
    uint64_t retry = 0;
    CHECK(getUint(o.body(), o.bodyLen(), EWP_K_ERROR_BODY_RETRY_AFTER_MS, retry));
    CHECK_EQ(retry, 250u);
    CHECK_EQ(rig.srv.inFlight(), 4u);

    gOpen.store(1);
    std::vector<Out> rest = drain(rig.up, 300);
    CHECK_EQ(rest.size(), 4u);
    for (const Out& r : rest) CHECK_EQ(r.h.type, EWP_MSG_RES);
    CHECK_EQ(rig.srv.inFlight(), 0u);

    // BUSY rid was not remembered: the gateway may retry the same rid.
    rig.send(req(5, kEcho));
    CHECK(pop(rig.up, o));
    CHECK_EQ(o.h.type, EWP_MSG_RES);
}

TEST(mcp_deadline_expires_while_queued) {
    Rig rig;
    rig.send(req(1, kGate));
    waitGateRunning();
    rig.send(req(2, kEcho, 0, 5));  // 5 ms budget
    os::delayMs(30);
    gOpen.store(1);
    std::vector<Out> v = drain(rig.up, 200);
    CHECK_EQ(v.size(), 2u);
    bool sawExpired = false;
    for (const Out& o : v) {
        if (o.h.rid == 2) {
            CHECK_EQ(o.h.type, EWP_MSG_ERR);
            CHECK_EQ(o.h.status, EWP_ST_DEADLINE_EXCEEDED);
            sawExpired = true;
        }
    }
    CHECK(sawExpired);
    CHECK_EQ(gEchoRuns.load(), 0);  // never executed
    CHECK_EQ(rig.srv.stats().expired.load(), 1u);
}

TEST(mcp_cancel_queued_job_never_runs) {
    Rig rig;
    rig.send(req(1, kGate));
    waitGateRunning();
    rig.send(req(2, kEcho));
    rig.send(cancel(2));
    gOpen.store(1);
    std::vector<Out> v = drain(rig.up, 200);
    CHECK_EQ(v.size(), 2u);
    for (const Out& o : v) {
        if (o.h.rid == 2) CHECK(o.h.type == EWP_MSG_ERR && o.h.status == EWP_ST_CANCELLED);
    }
    CHECK_EQ(gEchoRuns.load(), 0);
    CHECK_EQ(rig.srv.stats().cancels.load(), 1u);
}

TEST(mcp_cancel_running_job_is_seen_by_handler) {
    Rig rig;
    rig.send(req(9, kGate));
    waitGateRunning();
    rig.send(cancel(9));
    Out o;
    CHECK(pop(rig.up, o));
    CHECK_EQ(o.h.type, EWP_MSG_ERR);
    CHECK_EQ(o.h.status, EWP_ST_CANCELLED);
    std::string detail;
    CHECK(getText(o.body(), o.bodyLen(), EWP_K_ERROR_BODY_DETAIL, detail));
    CHECK_STR(detail.c_str(), "cancelled while running");
    CHECK(hasKey(o.body(), o.bodyLen(), EWP_K_ERROR_BODY_TIMING));  // failed mid-execution: timing attached
}

TEST(mcp_cancel_unknown_rid_is_ignored) {
    Rig rig;
    rig.send(cancel(12345));
    Out o;
    CHECK(!pop(rig.up, o, 50));
    CHECK_EQ(rig.srv.stats().cancels.load(), 0u);
}

TEST(mcp_priority_jumps_the_queue) {
    Rig rig;
    rig.send(req(1, kGate));
    waitGateRunning();
    rig.send(req(2, kEcho));
    rig.send(req(3, kEcho, EWP_FLAG_PRIORITY | EWP_FLAG_ACK_REQUESTED));
    Out ack;
    CHECK(pop(rig.up, ack));
    CHECK_EQ(ack.h.type, EWP_MSG_ACK);
    uint64_t depth = 99;
    CHECK(getUint(ack.body(), ack.bodyLen(), EWP_K_ACK_BODY_QUEUE_DEPTH, depth));
    CHECK_EQ(depth, 1u);  // only the running job is ahead of a PRIORITY request
    gOpen.store(1);
    std::vector<Out> v = drain(rig.up, 200);
    CHECK_EQ(v.size(), 3u);
    if (v.size() == 3) {
        CHECK_EQ(v[0].h.rid, 1u);  // the one that was running
        CHECK_EQ(v[1].h.rid, 3u);  // priority
        CHECK_EQ(v[2].h.rid, 2u);
    }
}

TEST(mcp_handler_failure_copies_stack_detail) {
    Rig rig;
    rig.send(req(4, kFail));
    Out o;
    CHECK(pop(rig.up, o));
    CHECK_EQ(o.h.type, EWP_MSG_ERR);
    CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);
    CHECK_EQ(o.h.tool, kFail);
    std::string detail;
    CHECK(getText(o.body(), o.bodyLen(), EWP_K_ERROR_BODY_DETAIL, detail));
    CHECK_STR(detail.c_str(), "channel 9 out of range");
    uint64_t argKey = 0;
    CHECK(getUint(o.body(), o.bodyLen(), EWP_K_ERROR_BODY_ARG_KEY, argKey));
    CHECK_EQ(argKey, 7u);
    CHECK_EQ(rig.srv.stats().failed.load(), 1u);
}

TEST(mcp_invalid_results_become_internal_errors) {
    Rig rig;
    for (uint8_t tool : {kBad, kBig, kNone}) {
        rig.send(req(100 + tool, tool));
        Out o;
        CHECK(pop(rig.up, o));
        CHECK_EQ(o.h.type, EWP_MSG_ERR);
        CHECK_EQ(o.h.status, EWP_ST_INTERNAL);
        CHECK(wellFormed(o.body(), o.bodyLen()));
    }
    CHECK_EQ(rig.up.freeSlots(), rig.up.slots());  // nothing leaked
}

TEST(mcp_broadcast_replies_are_jittered) {
    Rig rig(4, 80);
    const int64_t t0 = os::nowUs();
    rig.send(req(0x55, kEcho, EWP_FLAG_BROADCAST | EWP_FLAG_ACK_REQUESTED), ewp::Topic::Broadcast);
    Out ack, res;
    CHECK(pop(rig.up, ack, 500));
    CHECK(pop(rig.up, res, 500));
    CHECK_EQ(ack.h.type, EWP_MSG_ACK);  // ACK before RES even when both were parked
    CHECK_EQ(res.h.type, EWP_MSG_RES);
    CHECK(os::nowUs() - t0 <= 400 * 1000);
    // non-broadcast on the broadcast topic is refused
    rig.send(req(0x56, kEcho), ewp::Topic::Broadcast);
    Out o;
    CHECK(pop(rig.up, o, 300));
    CHECK_EQ(o.h.status, EWP_ST_BAD_FRAME);
}

TEST(mcp_oversized_answers_too_large) {
    Rig rig;
    Bytes f = req(0x99, kEcho);
    rig.srv.onOversized(f.data(), f.size(), ewp::Topic::Unicast);
    Out o;
    CHECK(pop(rig.up, o));
    CHECK_EQ(o.h.status, EWP_ST_TOO_LARGE);
    CHECK_EQ(o.h.rid, 0x99u);
}

TEST(mcp_frame_over_slot_size_is_too_large) {
    Rig rig;
    Bytes body(EWP_LIMIT_DOWNLINK_FRAME, 0x00);  // 16 + 4096 > 4096
    body[0] = 0x59;                              // bytes(4093) would be valid CBOR; size decides first
    Bytes f = req(0x9A, kEcho, 0, 0, body);
    rig.send(f);
    Out o;
    CHECK(pop(rig.up, o));
    CHECK_EQ(o.h.status, EWP_ST_TOO_LARGE);
}

namespace {
std::atomic<int> gTicks{0};
void onTick(void*) { gTicks.fetch_add(1); }
}  // namespace

TEST(mcp_tick_runs_on_worker_and_coalesces) {
    gTicks.store(0);
    mcp::Uplink up;
    CHECK(up.begin(4, 2048, false));
    mcp::MCPServer srv;
    CHECK(srv.registerTool({kEcho, 1, 0, "echo", &echoTool, nullptr}));
    srv.setTickHandler(&onTick, nullptr);
    mcp::ServerConfig cfg;
    cfg.workerCore = -1;
    CHECK(srv.begin(cfg, up));
    CHECK(srv.postTick());
    for (int i = 0; i < 1000 && gTicks.load() == 0; ++i) os::delayMs(1);
    CHECK_EQ(gTicks.load(), 1);
    srv.shutdown();
}

/* ---- concurrency stress: one MQTT-like producer, one consumer, the worker ---- */

TEST(mcp_stress_every_request_answered_once) {
    Rig rig(4, 0, 16);
    constexpr uint32_t kN = 3000;
    std::vector<uint8_t> answered(kN + 1, 0);
    std::atomic<bool> producing{true};
    std::atomic<uint32_t> frames{0};

    std::thread consumer([&] {
        Out o;
        for (;;) {
            if (pop(rig.up, o, 5)) {
                frames.fetch_add(1);
                if ((o.h.type == EWP_MSG_RES || o.h.type == EWP_MSG_ERR) && o.h.rid >= 1 && o.h.rid <= kN) {
                    answered[o.h.rid]++;
                }
                if (!wellFormed(o.body(), o.bodyLen())) ::check::fail(__FILE__, __LINE__, "malformed uplink frame");
                continue;
            }
            if (!producing.load()) break;
        }
    });

    for (uint32_t rid = 1; rid <= kN; ++rid) {
        const uint8_t flags = static_cast<uint8_t>((rid % 3 == 0 ? EWP_FLAG_ACK_REQUESTED : 0) |
                                                   (rid % 7 == 0 ? EWP_FLAG_PRIORITY : 0));
        const uint8_t tool = rid % 11 == 0 ? kFail : kEcho;
        rig.send(req(rid, tool, flags, 2000));
        if (rid % 5 == 0) rig.send(cancel(rid - 1));
    }
    os::delayMs(300);
    producing.store(false);
    consumer.join();

    uint32_t lost = 0, twice = 0;
    for (uint32_t rid = 1; rid <= kN; ++rid) {
        if (answered[rid] == 0) ++lost;
        if (answered[rid] > 1) ++twice;
    }
    // Every request gets exactly one RES/ERR - unless the reply itself was
    // dropped for lack of an uplink buffer, which the server counts.
    CHECK_EQ(twice, 0u);
    CHECK(lost <= rig.srv.stats().replyDropped.load());
    CHECK_EQ(rig.srv.inFlight(), 0u);
    CHECK_EQ(rig.up.freeSlots(), rig.up.slots());
    printf("    stress: %u frames, unanswered=%u busy=%u dropped-replies=%u cancels=%u\n", frames.load(), lost,
           rig.srv.stats().busy.load(), rig.srv.stats().replyDropped.load(), rig.srv.stats().cancels.load());
}
