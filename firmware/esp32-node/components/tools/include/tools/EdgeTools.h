/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/EdgeTools.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The Phase 3 edge tools and the periodic TELEMETRY publisher:
 *
 *   0x01 get_device_info  {}                 -> HelloBody            read-only, idempotent
 *   0x02 read_telemetry   {1: sections?}     -> TelemetryBody        read-only, idempotent
 *   0x06 identify         {1: durationMs?}   -> {}                   only when a LED GPIO is configured
 *
 * run_inference (0x03), set_actuator (0x04) and set_state_mode (0x05) are
 * registered by later phases through the same MCPServer::registerTool().
 *
 * All handlers and the tick run on the MCP worker task, one at a time, which
 * is why they can share one TelemetrySnapshot scratch buffer without a lock.
 */
#pragma once

#include <stdint.h>

#include <atomic>

#include "mcp/MCPServer.h"
#include "mcp/Uplink.h"
#include "tools/Identity.h"
#include "tools/Telemetry.h"

namespace tools {

/** Something that can visibly identify the board (a LED). */
class Indicator {
public:
    /** Start blinking for durationMs and return at once (never block the worker). */
    virtual void blink(uint32_t durationMs) = 0;

protected:
    ~Indicator() = default;
};

struct EdgeToolsConfig {
    const Identity* identity = nullptr;       // filled at boot, read-only afterwards
    TelemetrySource* source = nullptr;        // platform probes
    Indicator* indicator = nullptr;           // nullptr: identify is not offered
    mcp::Uplink* uplink = nullptr;            // where periodic TELEMETRY frames go
    uint8_t periodicSections = kSupportedSections;
    /** Skip periodic telemetry while the broker link is down (nothing would carry it). */
    bool (*linkUp)(void* user) = nullptr;
    void* linkUser = nullptr;
    uint8_t initialMode = EWP_MODE_ACTIVE;
};

class EdgeTools {
public:
    static constexpr uint32_t kIdentifyDefaultMs = 1200;
    static constexpr uint32_t kIdentifyMaxMs = 10000;

    /** Registers the tools and the tick handler. Call BEFORE server.begin(). */
    bool begin(const EdgeToolsConfig& cfg, mcp::MCPServer& server);

    uint8_t mode() const { return static_cast<uint8_t>(mode_.load(std::memory_order_relaxed)); }
    void setMode(uint8_t m) { mode_.store(m, std::memory_order_relaxed); }

    /** Per-boot sequence used as the rid of device-originated frames (HELLO, STATUS). */
    uint32_t nextDeviceRid() { return deviceRid_.fetch_add(1, std::memory_order_relaxed) + 1; }
    uint32_t telemetrySeq() const { return seq_.load(std::memory_order_relaxed); }
    uint32_t telemetrySkipped() const { return skipped_.load(std::memory_order_relaxed); }

    /** HelloBody for the retained HELLO (net_task) - same bytes as get_device_info. */
    void encodeHello(ewp::CborWriter& w) const;

    const Identity& identity() const { return *cfg_.identity; }

private:
    static void readTelemetry(mcp::ToolContext& ctx);
    static void getDeviceInfo(mcp::ToolContext& ctx);
    static void identify(mcp::ToolContext& ctx);
    static void onTick(void* self);

    EdgeToolsConfig cfg_;
    const mcp::MCPServer* server_ = nullptr;
    TelemetrySnapshot snap_;  // worker-only scratch (handlers and tick never run concurrently)
    std::atomic<uint32_t> mode_{EWP_MODE_ACTIVE};
    std::atomic<uint32_t> deviceRid_{0};
    std::atomic<uint32_t> seq_{0};
    std::atomic<uint32_t> skipped_{0};
};

}  // namespace tools
