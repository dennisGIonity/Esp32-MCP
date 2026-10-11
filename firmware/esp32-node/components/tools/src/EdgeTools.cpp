/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/EdgeTools.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "tools/EdgeTools.h"

#include "tools/Args.h"

namespace tools {

bool EdgeTools::begin(const EdgeToolsConfig& cfg, mcp::MCPServer& server) {
    if (cfg.identity == nullptr || cfg.source == nullptr || cfg.uplink == nullptr) return false;
    cfg_ = cfg;
    server_ = &server;
    mode_.store(cfg.initialMode, std::memory_order_relaxed);

    // read_telemetry: the reference registration of Phase 3. The handler runs on
    // the worker (Core 1); onDownlink() on Core 0 only copies the frame and queues it.
    bool ok = server.registerTool({EWP_TOOL_READ_TELEMETRY, 1, EWP_TOOL_FLAG_READ_ONLY | EWP_TOOL_FLAG_IDEMPOTENT,
                                   EWP_TOOL_NAME_READ_TELEMETRY, &EdgeTools::readTelemetry, this});
    ok = ok && server.registerTool({EWP_TOOL_GET_DEVICE_INFO, 1, EWP_TOOL_FLAG_READ_ONLY | EWP_TOOL_FLAG_IDEMPOTENT,
                                    EWP_TOOL_NAME_GET_DEVICE_INFO, &EdgeTools::getDeviceInfo, this});
    if (cfg.indicator != nullptr) {
        ok = ok && server.registerTool(
                       {EWP_TOOL_IDENTIFY, 1, 0, EWP_TOOL_NAME_IDENTIFY, &EdgeTools::identify, this});
    }
    server.setTickHandler(&EdgeTools::onTick, this);
    return ok;
}

void EdgeTools::encodeHello(ewp::CborWriter& w) const { encodeHelloBody(w, *cfg_.identity, mode(), *server_); }

/* ---- 0x02 read_telemetry ------------------------------------------------- */

void EdgeTools::readTelemetry(mcp::ToolContext& ctx) {
    EdgeTools* self = static_cast<EdgeTools*>(ctx.user());
    uint8_t sections = kDefaultSections;
    const bool ok = parseArgs(ctx, [&](uint64_t key, ewp::CborReader& r) {
        if (key == EWP_K_READ_TELEMETRY_ARGS_SECTIONS) {
            uint64_t v = 0;
            if (!r.readUint(v) || v > 0xFF) {
                ctx.fail(EWP_ST_INVALID_ARGS, "sections must be a TelemetrySection bitmask (0-255)",
                         EWP_K_READ_TELEMETRY_ARGS_SECTIONS);
                return false;
            }
            sections = static_cast<uint8_t>(v);
            return true;
        }
        return skipUnknownArg(r);
    });
    if (!ok) return;

    // Unsupported bits (INFERENCE, EXT) are ignored, not an error: the result
    // simply has no such section - exactly what the contract's optional fields say.
    sections &= kSupportedSections;
    TelemetrySnapshot& s = self->snap_;
    s = TelemetrySnapshot{};
    self->cfg_.source->collect(s, sections);
    s.seq = self->telemetrySeq();  // on-demand reads do not advance the periodic sequence
    s.mode = self->mode();
    encodeTelemetryBody(ctx.out(), s, sections);
}

/* ---- 0x01 get_device_info ------------------------------------------------ */

void EdgeTools::getDeviceInfo(mcp::ToolContext& ctx) {
    EdgeTools* self = static_cast<EdgeTools*>(ctx.user());
    if (!parseArgs(ctx, [](uint64_t, ewp::CborReader& r) { return skipUnknownArg(r); })) return;
    self->encodeHello(ctx.out());
}

/* ---- 0x06 identify --------------------------------------------------------- */

void EdgeTools::identify(mcp::ToolContext& ctx) {
    EdgeTools* self = static_cast<EdgeTools*>(ctx.user());
    uint32_t durationMs = kIdentifyDefaultMs;
    const bool ok = parseArgs(ctx, [&](uint64_t key, ewp::CborReader& r) {
        if (key == EWP_K_IDENTIFY_ARGS_DURATION_MS) {
            uint64_t v = 0;
            if (!r.readUint(v) || v == 0 || v > kIdentifyMaxMs) {
                ctx.fail(EWP_ST_INVALID_ARGS, "durationMs must be 1-10000", EWP_K_IDENTIFY_ARGS_DURATION_MS);
                return false;
            }
            durationMs = static_cast<uint32_t>(v);
            return true;
        }
        return skipUnknownArg(r);
    });
    if (!ok) return;
    self->cfg_.indicator->blink(durationMs);  // returns at once; a timer does the blinking
    ctx.out().map(0);                         // EmptyBody
}

/* ---- periodic TELEMETRY (worker tick) ---------------------------------------- */

void EdgeTools::onTick(void* p) {
    EdgeTools* self = static_cast<EdgeTools*>(p);
    if (self->cfg_.linkUp != nullptr && !self->cfg_.linkUp(self->cfg_.linkUser)) {
        self->skipped_.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    mcp::Uplink& up = *self->cfg_.uplink;
    const int slot = up.acquire(50);  // bounded: the worker also serves requests
    if (slot < 0) {
        up.noteDrop();
        return;
    }
    const uint8_t sections = self->cfg_.periodicSections & kSupportedSections;
    TelemetrySnapshot& s = self->snap_;
    s = TelemetrySnapshot{};
    self->cfg_.source->collect(s, sections);
    s.seq = self->seq_.fetch_add(1, std::memory_order_relaxed) + 1;  // a gap = QoS 0 loss, 1 = reboot
    s.mode = self->mode();
    const size_t len = buildDeviceFrame(up.buffer(slot), up.slotSize(), EWP_MSG_TELEMETRY, s.seq,
                                        [&](ewp::CborWriter& w) { encodeTelemetryBody(w, s, sections); });
    if (len == 0) {
        up.release(slot);
        up.noteDrop();
        return;
    }
    up.submit(slot, len, 0, 50);
}

}  // namespace tools
