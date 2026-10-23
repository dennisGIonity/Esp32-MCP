/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/SystemTools.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "tools/SystemTools.h"

#include "tools/Args.h"

namespace tools {

bool SystemTools::begin(const SystemToolsConfig& cfg, mcp::MCPServer& server) {
    if (cfg.scheduleRestart == nullptr) return false;
    cfg_ = cfg;
    bool ok = true;
    if (cfg.actuators != nullptr && cfg.actuators->count() > 0) {
        ok = server.registerTool(
            {EWP_TOOL_SET_ACTUATOR, 1, EWP_TOOL_FLAG_DESTRUCTIVE, EWP_TOOL_NAME_SET_ACTUATOR, &SystemTools::setActuator, this});
    }
    ok = ok && server.registerTool(
                   {EWP_TOOL_REBOOT, 1, EWP_TOOL_FLAG_DESTRUCTIVE, EWP_TOOL_NAME_REBOOT, &SystemTools::reboot, this});
    return ok;
}

void SystemTools::setActuator(mcp::ToolContext& ctx) {
    SystemTools* self = static_cast<SystemTools*>(ctx.user());
    bool haveCh = false;
    bool haveValue = false;
    uint64_t ch = 0;
    double value = 0;
    uint64_t rampMs = 0;
    uint64_t leaseMs = 0;
    const bool ok = parseArgs(ctx, [&](uint64_t key, ewp::CborReader& r) {
        switch (key) {
            case EWP_K_SET_ACTUATOR_ARGS_CHANNEL:
                haveCh = r.readUint(ch) && ch <= 0xFF;
                return haveCh;
            case EWP_K_SET_ACTUATOR_ARGS_VALUE:
                haveValue = r.readNumber(value);  // float fields may arrive as CBOR integers
                return haveValue;
            case EWP_K_SET_ACTUATOR_ARGS_RAMP_MS:
                return r.readUint(rampMs) && rampMs <= 0xFFFF;
            case EWP_K_SET_ACTUATOR_ARGS_LEASE_MS:
                return r.readUint(leaseMs) && leaseMs <= 0xFFFFFFFFu;
            default:
                return skipUnknownArg(r);
        }
    });
    if (!ok) return;
    if (!haveCh) return ctx.fail(EWP_ST_INVALID_ARGS, "channel is required", EWP_K_SET_ACTUATOR_ARGS_CHANNEL);
    if (!haveValue) return ctx.fail(EWP_ST_INVALID_ARGS, "value is required (0..1)", EWP_K_SET_ACTUATOR_ARGS_VALUE);
    const bool failsafe = self->cfg_.edge != nullptr && self->cfg_.edge->mode() == EWP_MODE_FAILSAFE;
    const ActuatorBank::SetOutcome o =
        self->cfg_.actuators->set(static_cast<uint8_t>(ch), static_cast<float>(value), static_cast<uint32_t>(rampMs),
                                  static_cast<uint32_t>(leaseMs), failsafe, os::nowUs());
    if (o.status != EWP_ST_OK) return ctx.fail(o.status, o.detail, o.argKey);
    ewp::CborMap m(ctx.out());
    m.key(EWP_K_SET_ACTUATOR_RESULT_CHANNEL).uint(ch);
    m.key(EWP_K_SET_ACTUATOR_RESULT_APPLIED).f32(o.applied);
    m.key(EWP_K_SET_ACTUATOR_RESULT_PREVIOUS).f32(o.previous);
    if (leaseMs > 0) m.key(EWP_K_SET_ACTUATOR_RESULT_LEASE_REMAINING_MS).uint(o.leaseRemainingMs);
}

void SystemTools::reboot(mcp::ToolContext& ctx) {
    SystemTools* self = static_cast<SystemTools*>(ctx.user());
    uint64_t delay = kRebootDefaultMs;
    const bool ok = parseArgs(ctx, [&](uint64_t key, ewp::CborReader& r) {
        if (key == EWP_K_REBOOT_ARGS_DELAY_MS) {
            if (!r.readUint(delay) || delay > kRebootMaxMs) {
                ctx.fail(EWP_ST_INVALID_ARGS, "delayMs must be 0-10000", EWP_K_REBOOT_ARGS_DELAY_MS);
                return false;
            }
            return true;
        }
        return skipUnknownArg(r);
    });
    if (!ok) return;
    // Never less than 300 ms: the RES has to leave through net_task first.
    const uint32_t inMs = delay < 300 ? 300 : static_cast<uint32_t>(delay);
    ewp::CborMap m(ctx.out());
    m.key(EWP_K_REBOOT_RESULT_IN_MS).uint(inMs);
    self->cfg_.scheduleRestart(self->cfg_.user, inMs);
}

}  // namespace tools
