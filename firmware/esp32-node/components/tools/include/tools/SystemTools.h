/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/SystemTools.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 *   0x04 set_actuator {1: channel, 2: value 0..1, 3: rampMs?, 4: leaseMs?}
 *                     -> {1: channel, 2: applied, 3: previous, 4: leaseRemainingMs?}   destructive
 *   0xF0 reboot       {1: delayMs? (default 500, max 10000)} -> {1: inMs}              destructive
 *
 * reboot answers first: the RES is queued before the restart is scheduled
 * (the platform hook waits delayMs, enough for net_task to publish it).
 * set_actuator is registered only when the board has at least one channel.
 */
#pragma once

#include <stdint.h>

#include "mcp/MCPServer.h"
#include "tools/Actuators.h"
#include "tools/EdgeTools.h"

namespace tools {

struct SystemToolsConfig {
    ActuatorBank* actuators = nullptr;  // nullptr / no channels: set_actuator not offered
    const EdgeTools* edge = nullptr;    // live StateMode (FAILSAFE locks the outputs)
    /** Schedule a restart in delayMs and return at once. */
    void (*scheduleRestart)(void* user, uint32_t delayMs) = nullptr;
    void* user = nullptr;
};

class SystemTools {
public:
    static constexpr uint32_t kRebootDefaultMs = 500;
    static constexpr uint32_t kRebootMaxMs = 10000;

    /** Call BEFORE server.begin(). */
    bool begin(const SystemToolsConfig& cfg, mcp::MCPServer& server);

private:
    static void setActuator(mcp::ToolContext& ctx);
    static void reboot(mcp::ToolContext& ctx);

    SystemToolsConfig cfg_;
};

}  // namespace tools
