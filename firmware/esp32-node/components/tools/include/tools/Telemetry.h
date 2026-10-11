/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/Telemetry.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * TelemetryBody (packages/protocol/src/telemetry.ts) - the periodic TELEMETRY
 * frame AND the read_telemetry result: one decoder, one Datadog mapping.
 *
 * Collection (platform-specific, TelemetrySource) is separated from encoding
 * (portable, encodeTelemetryBody) so the wire format is unit-tested on the
 * host against the TypeScript key tables.
 *
 * Every field maps to one ESP-IDF call (see EspPlatform.cpp):
 *   heap.internal*   heap_caps_get_{free_size,minimum_free_size,largest_free_block}(MALLOC_CAP_INTERNAL)
 *   heap.psram*      same with MALLOC_CAP_SPIRAM - ABSENT without PSRAM (ESP32-C6)
 *   heap.dmaFree     heap_caps_get_free_size(MALLOC_CAP_DMA)
 *   net.rssiDbm      esp_wifi_sta_get_ap_info()          - absent while not associated
 *   cpu[]            idle-task run-time deltas per core   - one element on single-core chips
 *   tasks[].stack    uxTaskGetSystemState() high-water marks, in BYTES on ESP-IDF
 *   sensors.dieTempC temperature_sensor_get_celsius()   - the die, not the room
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "ewp/cbor.h"
#include "ewp_protocol.h"

namespace tools {

/** read_telemetry without `sections`: bounded (< 300 B), what an agent usually wants. */
constexpr uint8_t kDefaultSections = EWP_TM_HEAP | EWP_TM_NET | EWP_TM_CPU | EWP_TM_ACTUATORS;
/** Sections this firmware can produce (INFERENCE / EXT arrive with run_inference, later phase). */
constexpr uint8_t kSupportedSections =
    EWP_TM_HEAP | EWP_TM_NET | EWP_TM_CPU | EWP_TM_TASKS | EWP_TM_SENSORS | EWP_TM_ACTUATORS;

constexpr size_t kMaxTaskStats = EWP_LIMIT_TASK_STATS;
constexpr size_t kMaxAdc = 4;
constexpr size_t kMaxActuators = EWP_LIMIT_ACTUATOR_CHANNELS;

struct HeapStats {
    uint32_t internalFree = 0;
    uint32_t internalMinFree = 0;
    uint32_t internalLargest = 0;
    bool hasPsram = false;
    uint32_t psramFree = 0;
    uint32_t psramMinFree = 0;
    uint32_t psramLargest = 0;
    bool hasDma = false;
    uint32_t dmaFree = 0;
};

struct NetStats {
    bool associated = false;
    int8_t rssiDbm = 0;
    uint8_t channel = 0;
    uint32_t txFail = 0;
    uint16_t reconnects = 0;
    uint8_t uplinkQueued = 0;
};

struct CpuStats {
    bool valid = false;  // false until two samples exist
    uint8_t cores = 1;
    uint8_t loadPct[2] = {0, 0};
};

struct TaskStat {
    uint8_t id;
    uint16_t stackFreeMin;  // bytes
    uint32_t runUsMax;
};

struct SensorStats {
    bool hasDieTemp = false;
    float dieTempC = 0.0f;
    uint8_t adcCount = 0;
    uint16_t adcMv[kMaxAdc] = {};
};

struct TelemetrySnapshot {
    uint32_t seq = 0;
    uint32_t uptimeS = 0;
    bool hasUnix = false;
    uint32_t unixS = 0;
    uint8_t mode = EWP_MODE_ACTIVE;
    HeapStats heap;
    NetStats net;
    CpuStats cpu;
    TaskStat tasks[kMaxTaskStats] = {};
    uint8_t taskCount = 0;
    SensorStats sensors;
    float actuators[kMaxActuators] = {};
    uint8_t actuatorCount = 0;
};

/**
 * Encode a TelemetryBody with the requested sections. seq, uptimeS and mode
 * are always present (they are required in the contract). Sections the
 * snapshot cannot provide (e.g. CPU before the second sample) are omitted,
 * never sent as zeros that would look like real readings.
 */
void encodeTelemetryBody(ewp::CborWriter& w, const TelemetrySnapshot& s, uint8_t sections);

/** Platform hook: fill the requested sections. Runs on the MCP worker task. */
class TelemetrySource {
public:
    virtual void collect(TelemetrySnapshot& out, uint8_t sections) = 0;

protected:
    ~TelemetrySource() = default;  // never deleted through the interface
};

}  // namespace tools
