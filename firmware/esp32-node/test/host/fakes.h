/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/fakes.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Deterministic stand-ins for the ESP-IDF probes (host tests and the interop
 * frame dumper). Values are realistic for an ESP32-S3 N16R8 on Wi-Fi.
 */
#pragma once

#include <string.h>

#include <atomic>

#include "tools/EdgeTools.h"
#include "tools/Identity.h"
#include "tools/Telemetry.h"

namespace fakes {

class Source final : public tools::TelemetrySource {
public:
    std::atomic<int> calls{0};
    void collect(tools::TelemetrySnapshot& s, uint8_t sections) override {
        calls.fetch_add(1);
        s.uptimeS = 3600;
        s.hasUnix = true;
        s.unixS = 1791763200;  // 2026-10-11T00:00:00Z
        if (sections & EWP_TM_HEAP) {
            s.heap.internalFree = 182344;
            s.heap.internalMinFree = 150112;
            s.heap.internalLargest = 110592;
            s.heap.hasPsram = true;
            s.heap.psramFree = 8302336;
            s.heap.psramMinFree = 8290000;
            s.heap.psramLargest = 8257536;
            s.heap.hasDma = true;
            s.heap.dmaFree = 170000;
        }
        if (sections & EWP_TM_NET) {
            s.net.associated = true;
            s.net.rssiDbm = -61;
            s.net.channel = 6;
            s.net.txFail = 2;
            s.net.reconnects = 1;
            s.net.uplinkQueued = 0;
        }
        if (sections & EWP_TM_CPU) {
            s.cpu.valid = true;
            s.cpu.cores = 2;
            s.cpu.loadPct[0] = 12;
            s.cpu.loadPct[1] = 3;
        }
        if (sections & EWP_TM_TASKS) {
            s.taskCount = 3;
            s.tasks[0] = {1, 2200, 900};
            s.tasks[1] = {2, 3100, 1450};
            s.tasks[2] = {3, 1800, 300};
        }
        if (sections & EWP_TM_SENSORS) {
            s.sensors.hasDieTemp = true;
            s.sensors.dieTempC = 41.5f;
            s.sensors.adcCount = 1;
            s.sensors.adcMv[0] = 1650;
        }
    }
};

class Led final : public tools::Indicator {
public:
    std::atomic<uint32_t> lastMs{0};
    std::atomic<int> blinks{0};
    void blink(uint32_t durationMs) override {
        lastMs.store(durationMs);
        blinks.fetch_add(1);
    }
};

inline void identity(tools::Identity& id) {
    const uint8_t mac[6] = {0x98, 0xa3, 0x16, 0xe5, 0xd1, 0x8c};
    tools::formatHwid(mac, id.hwid, sizeof id.hwid);
    tools::parseUuid("0192f1c4-7d2a-7b3e-9a10-4c5d6e7f8a9b", id.uuid);
    strcpy(id.fwVersion, "3.0.0");
    const uint8_t sha[8] = {0x1a, 0x2b, 0x3c, 0x4d, 0x5e, 0x6f, 0x70, 0x81};
    memcpy(id.elfSha, sha, sizeof sha);
    strcpy(id.idf, "v5.5.5");
    id.chipModel = EWP_CHIP_ESP32_S3;
    id.chipRev = 2;
    id.cores = 2;
    id.flashMb = 16;
    id.psramMb = 8;
    strcpy(id.site, "lab");
    strcpy(id.group, "bench");
    strcpy(id.label, "riser GF");
    id.resetReason = EWP_RESET_POWER_ON;
    id.bootCount = 7;
    id.addTask(1, "net_task", 0, 4096);
    id.addTask(2, "mcp_worker", 1, 6144);
    id.addTask(3, "prov_console", 0, 4096);
}

}  // namespace fakes
