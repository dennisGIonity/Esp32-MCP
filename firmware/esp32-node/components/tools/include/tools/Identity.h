/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/Identity.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Who this board is (HelloBody in packages/protocol/src/hello.ts) and the
 * encoders for the device-originated HELLO / STATUS frames.
 *
 * The Identity is filled ONCE at boot (EspPlatform.cpp) and is read-only
 * afterwards, so the MCP worker (get_device_info) and net_task (HELLO on
 * connect) read it without a lock. site/group/label therefore take effect at
 * the next boot - the same moment the MQTT topics change - and `set` over the
 * serial console reports reboot_required for them.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "ewp/cbor.h"
#include "ewp/frame.h"
#include "ewp_protocol.h"

namespace mcp {
class MCPServer;
}

namespace tools {

constexpr size_t kMaxTasks = EWP_LIMIT_TASK_STATS;

/** [task id, FreeRTOS task name, core (0, 1, 0xFF = unpinned), stack bytes] */
struct TaskInfo {
    uint8_t id;
    char name[16];  // configMAX_TASK_NAME_LEN on ESP-IDF is 16 incl. NUL
    uint8_t core;
    uint16_t stackBytes;
};

struct Identity {
    char hwid[24] = {};     // "esp32-" + 12 hex of the Wi-Fi STA (base) MAC; MQTT client id + topic level
    uint8_t uuid[16] = {};  // provisioned RFC 9562 UUID, all zero = not provisioned (nil UUID)
    char fwVersion[17] = {};
    uint8_t elfSha[8] = {};
    char idf[17] = {};
    uint8_t chipModel = EWP_CHIP_OTHER;
    uint16_t chipRev = 0;
    uint8_t cores = 1;
    uint8_t flashMb = 0;
    uint8_t psramMb = 0;
    uint16_t maxDownlink = EWP_LIMIT_DOWNLINK_FRAME;
    uint16_t maxUplink = 2048;
    uint8_t maxInflight = EWP_LIMIT_MAX_INFLIGHT;
    char site[33] = {};
    char group[33] = {};
    char label[49] = {};
    uint8_t resetReason = EWP_RESET_UNKNOWN;
    uint32_t bootCount = 0;
    TaskInfo tasks[kMaxTasks] = {};
    uint8_t taskCount = 0;

    /** Register a task for HELLO.tasks / TELEMETRY.tasks. Boot only. False when full. */
    bool addTask(uint8_t id, const char* name, int core, uint32_t stackBytes);
};

/** HelloBody (also the get_device_info result). `mode` is the live StateMode. */
void encodeHelloBody(ewp::CborWriter& w, const Identity& id, uint8_t mode, const mcp::MCPServer& server);

/** StatusBody. mode < 0 omits it; ip may be nullptr (ipLen 4 or 16); wakeAtS 0 omits it. */
void encodeStatusBody(ewp::CborWriter& w, uint8_t state, int mode, const uint8_t* ip, size_t ipLen, uint32_t wakeAtS);

/**
 * Build a complete device-originated frame (tool = NO_TOOL, status = OK) in
 * `buf`: header + whatever `fill(writer)` encodes. Returns the frame length,
 * or 0 when the body did not fit (nothing usable is left in buf then).
 */
template <typename Fill>
size_t buildDeviceFrame(uint8_t* buf, size_t cap, uint8_t type, uint32_t rid, Fill&& fill) {
    if (buf == nullptr || cap <= EWP_HEADER_LEN) return 0;
    ewp::CborWriter w(buf + EWP_HEADER_LEN, cap - EWP_HEADER_LEN);
    fill(w);
    if (!w.ok() || w.size() > 0xFFFFu) return 0;
    ewp::Header h;
    h.type = type;
    h.rid = rid;
    h.tool = EWP_NO_TOOL;
    h.status = EWP_ST_OK;
    h.bodyLen = static_cast<uint16_t>(w.size());
    ewp::encodeHeader(h, buf);
    return EWP_HEADER_LEN + w.size();
}

/** Parse a canonical UUID string ("0192f1c4-7d2a-7b3e-9a10-4c5d6e7f8a9b") into 16 bytes. */
bool parseUuid(const char* text, uint8_t out[16]);

/** "esp32-" + 12 lowercase hex digits; `out` needs 19 bytes. */
void formatHwid(const uint8_t mac[6], char* out, size_t outLen);

}  // namespace tools
