/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | main/NodeApp.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Application glue between the components: which settings are applied to the
 * live Wi-Fi/MQTT link, the link supervisor that runs inside net_task, the
 * live counters for TELEMETRY, and the ESP side of the provisioning protocol.
 * main.cpp owns the boot order; this file owns the policies.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <atomic>

#include "mcp/MCPServer.h"
#include "mcp/Uplink.h"
#include "mcp/os.h"
#include "prov/Config.h"
#include "prov/ProvHandler.h"
#include "tools/EdgeTools.h"
#include "tools/Telemetry.h"

namespace app {

/** HELLO.tasks / TELEMETRY.tasks ids (stable: the gateway keys Datadog tags on them). */
enum TaskId : uint8_t {
    kTaskNet = 1,       // net_task: publisher + link supervisor (Core 0)
    kTaskWorker = 2,    // mcp_worker: tool handlers + telemetry (Core 1)
    kTaskConsole = 3,   // prov_console: serial line reader (Core 0)
    kTaskProv = 4,      // prov_worker: scan / test (any core)
    kTaskMqtt = 5,      // mqtt_task: esp-mqtt receive path (Core 0)
    kTaskTcpip = 6,     // tiT: lwIP
    kTaskEvents = 7,    // sys_evt: default event loop
    kTaskTimer = 8,     // esp_timer
};

/** Everything the glue needs, set once in main.cpp before any task starts. */
struct Wiring {
    prov::ConfigStore* cfg = nullptr;
    mcp::MCPServer* server = nullptr;
    mcp::Uplink* uplink = nullptr;
    tools::EdgeTools* edge = nullptr;
    prov::ProvHandler* prov = nullptr;
    const char* hwid = nullptr;
    bool autoStartLink = false;  // provisioned node at boot: start MQTT on the first address
};

void wire(const Wiring& w);

/** Apply the stored Wi-Fi settings to the radio (remembers what was applied). */
void connectWifi(const prov::NodeConfig& c);
bool wifiApplied(const prov::NodeConfig& c);

/** (Re)start the MQTT link with these settings (CA loaded from NVS). Serialised. Not from the MQTT task. */
bool startLink(const prov::NodeConfig& c);
bool linkApplied(const prov::NodeConfig& c);

/** net_task hook: start the link and SNTP on the first address. */
void superviseLink(void* unused);

/** TELEMETRY hooks. */
void netStats(void* unused, tools::NetStats& out);
uint32_t taskRunUsMax(void* unused, uint8_t taskId);

/** ca_pem deep check (mbedTLS X.509 parse). */
bool x509Check(const char* pemNulTerminated, size_t len);

/** Announce REBOOTING, flush the console, wait >= 100 ms, restart. */
[[noreturn]] void restartNow();

/** The ESP side of ionity-prov/2: live state, scan/test on a worker task, restart. */
class EspProvBackend final : public prov::ProvBackend {
public:
    bool begin(unsigned priority, uint32_t stackBytes);
    void live(prov::LiveState& out) override;
    bool startScan() override;
    bool startTest(uint32_t timeoutMs) override;
    void configChanged(uint32_t changedFields) override;
    bool factoryResetWifi() override;
    void restart() override;
    uint32_t runUsMax() const { return runUsMax_.load(std::memory_order_relaxed); }

private:
    struct Job {
        uint8_t op;  // 1 scan, 2 test
        uint32_t timeoutMs;
    };
    static void taskEntry(void* self);
    void runScan();
    void runTest(uint32_t timeoutMs);

    os::TypedQueue<Job> jobs_;
    std::atomic<bool> busy_{false};
    std::atomic<uint32_t> runUsMax_{0};
};

}  // namespace app
