/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/Telemetry.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "tools/Telemetry.h"

#include <math.h>

namespace tools {

void encodeTelemetryBody(ewp::CborWriter& w, const TelemetrySnapshot& s, uint8_t sections) {
    ewp::CborMap m(w);
    m.key(EWP_K_TELEMETRY_BODY_SEQ).uint(s.seq);
    m.key(EWP_K_TELEMETRY_BODY_UPTIME_S).uint(s.uptimeS);
    if (s.hasUnix) m.key(EWP_K_TELEMETRY_BODY_UNIX_S).uint(s.unixS);
    m.key(EWP_K_TELEMETRY_BODY_MODE).uint(s.mode);

    if (sections & EWP_TM_HEAP) {
        ewp::CborMap h(m.key(EWP_K_TELEMETRY_BODY_HEAP));
        h.key(EWP_K_HEAP_STATS_INTERNAL_FREE).uint(s.heap.internalFree);
        h.key(EWP_K_HEAP_STATS_INTERNAL_MIN_FREE).uint(s.heap.internalMinFree);
        h.key(EWP_K_HEAP_STATS_INTERNAL_LARGEST).uint(s.heap.internalLargest);
        if (s.heap.hasPsram) {
            h.key(EWP_K_HEAP_STATS_PSRAM_FREE).uint(s.heap.psramFree);
            h.key(EWP_K_HEAP_STATS_PSRAM_MIN_FREE).uint(s.heap.psramMinFree);
            h.key(EWP_K_HEAP_STATS_PSRAM_LARGEST).uint(s.heap.psramLargest);
        }
        if (s.heap.hasDma) h.key(EWP_K_HEAP_STATS_DMA_FREE).uint(s.heap.dmaFree);
    }

    if (sections & EWP_TM_NET) {
        ewp::CborMap n(m.key(EWP_K_TELEMETRY_BODY_NET));
        if (s.net.associated) {
            n.key(EWP_K_NET_STATS_RSSI_DBM).sint(s.net.rssiDbm);
            n.key(EWP_K_NET_STATS_CHANNEL).uint(s.net.channel);
        }
        n.key(EWP_K_NET_STATS_TX_FAIL).uint(s.net.txFail);
        n.key(EWP_K_NET_STATS_RECONNECTS).uint(s.net.reconnects);
        n.key(EWP_K_NET_STATS_UPLINK_QUEUED).uint(s.net.uplinkQueued);
    }

    if ((sections & EWP_TM_CPU) && s.cpu.valid) {
        const uint8_t cores = s.cpu.cores >= 2 ? 2 : 1;
        m.key(EWP_K_TELEMETRY_BODY_CPU).array(cores);
        for (uint8_t c = 0; c < cores; ++c) w.uint(s.cpu.loadPct[c] > 100 ? 100 : s.cpu.loadPct[c]);
    }

    if (sections & EWP_TM_TASKS) {
        const uint8_t n = s.taskCount > kMaxTaskStats ? static_cast<uint8_t>(kMaxTaskStats) : s.taskCount;
        m.key(EWP_K_TELEMETRY_BODY_TASKS).array(n);
        for (uint8_t i = 0; i < n; ++i) {
            w.array(3);  // [id, stackFreeMin bytes, runUsMax]
            w.uint(s.tasks[i].id);
            w.uint(s.tasks[i].stackFreeMin);
            w.uint(s.tasks[i].runUsMax);
        }
    }

    if (sections & EWP_TM_SENSORS) {
        ewp::CborMap sm(m.key(EWP_K_TELEMETRY_BODY_SENSORS));
        if (s.sensors.hasDieTemp && isfinite(s.sensors.dieTempC)) {
            sm.key(EWP_K_SENSOR_STATS_DIE_TEMP_C).f32(s.sensors.dieTempC);
        }
        if (s.sensors.adcCount > 0) {
            const uint8_t n = s.sensors.adcCount > kMaxAdc ? static_cast<uint8_t>(kMaxAdc) : s.sensors.adcCount;
            sm.key(EWP_K_SENSOR_STATS_ADC_MV).array(n);
            for (uint8_t i = 0; i < n; ++i) w.uint(s.sensors.adcMv[i]);
        }
    }

    if (sections & EWP_TM_ACTUATORS) {
        const uint8_t n = s.actuatorCount > kMaxActuators ? static_cast<uint8_t>(kMaxActuators) : s.actuatorCount;
        m.key(EWP_K_TELEMETRY_BODY_ACTUATORS).array(n);
        for (uint8_t i = 0; i < n; ++i) w.f32(s.actuators[i]);
    }
}

}  // namespace tools
