/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/Identity.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "tools/Identity.h"

#include <stdio.h>
#include <string.h>

#include "mcp/MCPServer.h"

namespace tools {
namespace {

int hexValue(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

/** strnlen-bounded text: never reads past the fixed array even if it lost its NUL. */
void textField(ewp::CborWriter& w, const char* s, size_t cap) { w.text(s, strnlen(s, cap)); }

}  // namespace

bool Identity::addTask(uint8_t id, const char* name, int core, uint32_t stackBytes) {
    if (taskCount >= kMaxTasks || name == nullptr) return false;
    TaskInfo& t = tasks[taskCount++];
    t.id = id;
    snprintf(t.name, sizeof t.name, "%s", name);
    t.core = core < 0 ? 0xFF : static_cast<uint8_t>(core);
    t.stackBytes = stackBytes > 0xFFFFu ? 0xFFFF : static_cast<uint16_t>(stackBytes);
    return true;
}

void encodeHelloBody(ewp::CborWriter& w, const Identity& id, uint8_t mode, const mcp::MCPServer& server) {
    ewp::CborMap m(w);
    m.key(EWP_K_HELLO_BODY_PROTO).array(2);
    w.uint(EWP_VERSION);  // min
    w.uint(EWP_VERSION);  // max
    textField(m.key(EWP_K_HELLO_BODY_HWID), id.hwid, sizeof id.hwid);
    m.key(EWP_K_HELLO_BODY_UUID).bytes(id.uuid, sizeof id.uuid);
    {
        ewp::CborMap fw(m.key(EWP_K_HELLO_BODY_FW));
        textField(fw.key(EWP_K_FIRMWARE_INFO_VERSION), id.fwVersion, 16);
        fw.key(EWP_K_FIRMWARE_INFO_ELF_SHA).bytes(id.elfSha, sizeof id.elfSha);
        textField(fw.key(EWP_K_FIRMWARE_INFO_IDF), id.idf, 16);
    }
    {
        ewp::CborMap c(m.key(EWP_K_HELLO_BODY_CHIP));
        c.key(EWP_K_CHIP_INFO_MODEL).uint(id.chipModel);
        c.key(EWP_K_CHIP_INFO_REV).uint(id.chipRev);
        c.key(EWP_K_CHIP_INFO_CORES).uint(id.cores);
        c.key(EWP_K_CHIP_INFO_FLASH_MB).uint(id.flashMb);
        c.key(EWP_K_CHIP_INFO_PSRAM_MB).uint(id.psramMb);
    }
    {
        ewp::CborMap l(m.key(EWP_K_HELLO_BODY_LIMITS));
        l.key(EWP_K_LINK_LIMITS_MAX_DOWNLINK).uint(id.maxDownlink);
        l.key(EWP_K_LINK_LIMITS_MAX_UPLINK).uint(id.maxUplink);
        l.key(EWP_K_LINK_LIMITS_MAX_INFLIGHT).uint(id.maxInflight);
    }
    m.key(EWP_K_HELLO_BODY_TOOLS).array(static_cast<uint32_t>(server.toolCount()));
    for (size_t i = 0; i < server.toolCount(); ++i) {
        const mcp::ToolSpec* t = server.toolAt(i);
        w.array(3);  // [id, contract version, ToolFlag bitset]
        w.uint(t->id);
        w.uint(t->version);
        w.uint(t->flags);
    }
    m.key(EWP_K_HELLO_BODY_MODELS).array(0);     // run_inference: later phase
    m.key(EWP_K_HELLO_BODY_ACTUATORS).array(0);  // set_actuator: later phase
    m.key(EWP_K_HELLO_BODY_METRICS).array(0);
    m.key(EWP_K_HELLO_BODY_TASKS).array(id.taskCount);
    for (uint8_t i = 0; i < id.taskCount && i < kMaxTasks; ++i) {
        const TaskInfo& t = id.tasks[i];
        w.array(4);
        w.uint(t.id);
        textField(w, t.name, sizeof t.name);
        w.uint(t.core);
        w.uint(t.stackBytes);
    }
    m.key(EWP_K_HELLO_BODY_MODE).uint(mode);
    if (id.site[0] != '\0') textField(m.key(EWP_K_HELLO_BODY_SITE), id.site, 32);
    if (id.group[0] != '\0') textField(m.key(EWP_K_HELLO_BODY_GROUP), id.group, 32);
    if (id.label[0] != '\0') textField(m.key(EWP_K_HELLO_BODY_LABEL), id.label, 48);
    m.key(EWP_K_HELLO_BODY_RESET_REASON).uint(id.resetReason);
    m.key(EWP_K_HELLO_BODY_BOOT_COUNT).uint(id.bootCount);
}

void encodeStatusBody(ewp::CborWriter& w, uint8_t state, int mode, const uint8_t* ip, size_t ipLen, uint32_t wakeAtS) {
    ewp::CborMap m(w);
    m.key(EWP_K_STATUS_BODY_STATE).uint(state);
    if (mode >= 0) m.key(EWP_K_STATUS_BODY_MODE).uint(static_cast<uint64_t>(mode));
    if (wakeAtS != 0) m.key(EWP_K_STATUS_BODY_WAKE_AT_S).uint(wakeAtS);
    if (ip != nullptr && (ipLen == 4 || ipLen == 16)) m.key(EWP_K_STATUS_BODY_IP).bytes(ip, ipLen);
}

bool parseUuid(const char* text, uint8_t out[16]) {
    if (text == nullptr || strlen(text) != 36) return false;
    size_t o = 0;
    for (size_t i = 0; i < 36;) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (text[i] != '-') return false;
            ++i;
            continue;
        }
        const int hi = hexValue(text[i]);
        const int lo = hexValue(text[i + 1]);
        if (hi < 0 || lo < 0 || o >= 16) return false;
        out[o++] = static_cast<uint8_t>((hi << 4) | lo);
        i += 2;
    }
    return o == 16;
}

void formatHwid(const uint8_t mac[6], char* out, size_t outLen) {
    snprintf(out, outLen, "esp32-%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

}  // namespace tools
