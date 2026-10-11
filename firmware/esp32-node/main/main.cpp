/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | main/main.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Boot order and task topology of the ESP32-MCP node (ESP32-S3 primary,
 * ESP32-C6 secondary). Builds with PlatformIO (framework = espidf,
 * src_dir = main) and with idf.py - see README.md.
 *
 *            Core 0 - network                               Core 1 - tools
 *   +--------------------------------------------+   +---------------------------+
 *   | wifi (23)  tiT/lwIP (18)  sys_evt (20)      |   |                           |
 *   | mqtt_task (5)  -- MQTT_EVENT_DATA -------+  |   |  mcp_worker (5)           |
 *   |   MCPServer::onDownlink():               |  |   |   job_q.receive()         |
 *   |   header checks, memcpy into a job slot, +--+-->|   handler(ctx) e.g.       |
 *   |   job_q.send(slot, 0 ticks) / ERR BUSY   |  |   |   read_telemetry          |
 *   | net_task (6)  <-- tx_q (Uplink) ---------+--+---|   result -> uplink buffer |
 *   |   esp_mqtt_client_enqueue(): sole publisher |   |   tick -> TELEMETRY frame |
 *   | prov_console (3)  serial lines -> ionity-prov/2 | +---------------------------+
 *   | esp_timer (22): telemetry tick -> postTick() (0 ticks)
 *   +--------------------------------------------+
 *   ESP32-C6 (one core): the same tasks unpinned; net_task (6) > mcp_worker (4).
 *
 * The network side never waits for a tool: every hand-over from Core 0 uses a
 * zero timeout and back-pressure becomes an ERR BUSY frame (Phase 1 rule 2).
 *
 * MEMORY: every long-lived object below has static storage; the job pool and
 * the uplink pool are allocated once (PSRAM when present) before the first
 * frame can arrive. Nothing on the request path allocates (rule 1).
 */
#include <stdio.h>
#include <string.h>

#include "NodeApp.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "mcp/MCPServer.h"
#include "mcp/Uplink.h"
#include "net/MqttLink.h"
#include "net/WifiManager.h"
#include "prov/Config.h"
#include "prov/Console.h"
#include "prov/EspNvs.h"
#include "prov/ProvHandler.h"
#include "sdkconfig.h"
#include "tools/EdgeTools.h"
#include "tools/EspPlatform.h"
#include "tools/Identity.h"

namespace {

const char* const TAG = "ionity";

/* ---- task topology ---------------------------------------------------------- */
#if CONFIG_FREERTOS_NUMBER_OF_CORES > 1
constexpr int kNetCore = 0;   // Wi-Fi, lwIP, esp-mqtt, net_task, console
constexpr int kToolCore = 1;  // mcp_worker only: inference stages never delay the radio
constexpr unsigned kWorkerPrio = 5;
#else
constexpr int kNetCore = -1;  // ESP32-C6: one core, priorities decide
constexpr int kToolCore = -1;
constexpr unsigned kWorkerPrio = 4;  // below net_task (Phase 1 rule 9)
#endif
constexpr unsigned kNetPrio = 6;      // above mqtt_task (esp-mqtt default priority 5)
constexpr unsigned kConsolePrio = 3;
constexpr unsigned kProvPrio = 2;

// Kconfig booleans that are "n" are simply not defined: map them to constants once.
#ifdef CONFIG_IONITY_WIFI_POWER_SAVE
constexpr bool kWifiPowerSave = true;
#else
constexpr bool kWifiPowerSave = false;
#endif
#ifdef CONFIG_IONITY_LED_ACTIVE_HIGH
constexpr bool kLedActiveHigh = true;
#else
constexpr bool kLedActiveHigh = false;
#endif
#ifdef CONFIG_MQTT_TASK_STACK_SIZE
constexpr uint32_t kMqttStack = CONFIG_MQTT_TASK_STACK_SIZE;
#else
constexpr uint32_t kMqttStack = 6144;  // esp-mqtt default (MQTT_TASK_STACK_SIZE is a custom-config option)
#endif

// ESP-IDF stack sizes are in BYTES. Measured high-water marks are in TELEMETRY.tasks.
constexpr uint32_t kNetStack = 5120;
constexpr uint32_t kWorkerStack = 6144;
constexpr uint32_t kConsoleStack = 6144;
constexpr uint32_t kProvStack = 4096;

/* ---- long-lived objects (static storage: never freed, no heap churn) ------------ */
prov::NvsKv s_kv;
prov::ConfigStore s_cfg;
tools::Identity s_identity;
mcp::Uplink s_uplink;           // device -> gateway frame pool + tx_q (net_task drains it)
mcp::MCPServer s_server;        // tool registry + job pool + mcp_worker
tools::EdgeTools s_edge;        // read_telemetry, get_device_info, identify, periodic TELEMETRY
tools::EspTelemetrySource s_probes;
tools::GpioIndicator s_led;
prov::ProvHandler s_prov;
app::EspProvBackend s_provBackend;
esp_timer_handle_t s_telemetryTimer = nullptr;
uint32_t s_telemetryTicks = 0;  // esp_timer task only

/** esp_timer callback (esp_timer task, Core 0): hand the work to Core 1 and return. */
void onTelemetryTimer(void*) {
    ++s_telemetryTicks;
    // STANDBY reports once a minute instead of every period (fw 2.x behaviour).
    if (s_edge.mode() == EWP_MODE_STANDBY && (s_telemetryTicks % 6) != 0) return;
    s_server.postTick();  // 0-tick post; coalesces if the previous tick is still queued
}

const char* chipName(uint8_t model) {
    switch (model) {
        case EWP_CHIP_ESP32: return "ESP32";
        case EWP_CHIP_ESP32_S2: return "ESP32-S2";
        case EWP_CHIP_ESP32_S3: return "ESP32-S3";
        case EWP_CHIP_ESP32_C3: return "ESP32-C3";
        case EWP_CHIP_ESP32_C6: return "ESP32-C6";
        case EWP_CHIP_ESP32_C5: return "ESP32-C5";
        case EWP_CHIP_ESP32_H2: return "ESP32-H2";
        case EWP_CHIP_ESP32_P4: return "ESP32-P4";
        default: return "ESP32";
    }
}

void logLoadReport(const prov::LoadReport& r, const prov::NodeConfig& c) {
    if (r.migratedHostUrl) ESP_LOGI(TAG, "fw 2.x settings migrated: host_url=%s", c.hostUrl);
    if (r.hostUrlInvalid) ESP_LOGW(TAG, "stored MCP host is not a valid URL - using mDNS discovery");
    if (r.siteSanitized) ESP_LOGW(TAG, "stored site is not a valid topic level - using \"%s\"", c.site);
    if (r.groupSanitized) ESP_LOGW(TAG, "stored group is not a valid topic level - using \"%s\"", c.group);
    if (r.nvsError) ESP_LOGW(TAG, "some settings could not be read from NVS (see above)");
    // Never log secrets: only whether they are set.
    ESP_LOGI(TAG, "config: wifi=\"%s\" host=%s role=%s site=%s boot#%u mcp_token=%s", c.ssid,
             c.hostUrl[0] ? c.hostUrl : "mdns:ionity-fleet.local", c.role, c.site, static_cast<unsigned>(c.bootCount),
             c.mcpToken[0] ? "set" : "none");
}

}  // namespace

extern "C" void app_main(void) {
    // 1. Console first: from here on every log line is written whole and scrubbed of
    //    secrets, and provisioning replies can never be split by another task's log.
    prov::Console& console = prov::Console::instance();
    if (!console.begin()) ESP_LOGE(TAG, "console driver install failed - provisioning unavailable");

    // 2. NVS + configuration (fw 2.x keys read as-is, server+mqtt_port -> host_url once).
    bool nvsErased = false;
    ESP_ERROR_CHECK(prov::initNvsFlash(&nvsErased));
    s_cfg.begin(s_kv);
    prov::ConfigStore::setCertCheck(&app::x509Check);
    prov::LoadReport report;
    s_cfg.load(report);
    prov::NodeConfig node;
    s_cfg.snapshot(node);
    console.setSecrets(node);
    logLoadReport(report, node);

    // 3. Identity (read-only after this point: HELLO and get_device_info read it lock-free).
    tools::fillChipIdentity(s_identity);
    snprintf(s_identity.site, sizeof s_identity.site, "%s", node.site);
    snprintf(s_identity.group, sizeof s_identity.group, "%s", node.group);
    snprintf(s_identity.label, sizeof s_identity.label, "%s", node.label);
    if (node.deviceUuid[0] != '\0') tools::parseUuid(node.deviceUuid, s_identity.uuid);
    s_identity.bootCount = node.bootCount;
    s_identity.maxUplink = 2048;
    s_identity.maxInflight = CONFIG_IONITY_JOB_SLOTS;
    s_identity.addTask(app::kTaskNet, "net_task", kNetCore, kNetStack);
    s_identity.addTask(app::kTaskWorker, "mcp_worker", kToolCore, kWorkerStack);
    s_identity.addTask(app::kTaskConsole, "prov_console", kNetCore, kConsoleStack);
    s_identity.addTask(app::kTaskProv, "prov_worker", -1, kProvStack);
    s_identity.addTask(app::kTaskMqtt, "mqtt_task", kNetCore, kMqttStack);
    s_identity.addTask(app::kTaskTcpip, "tiT", kNetCore, CONFIG_LWIP_TCPIP_TASK_STACK_SIZE);
    s_identity.addTask(app::kTaskEvents, "sys_evt", -1, CONFIG_ESP_SYSTEM_EVENT_TASK_STACK_SIZE);
    s_identity.addTask(app::kTaskTimer, "esp_timer", 0, CONFIG_ESP_TIMER_TASK_STACK_SIZE);
    ESP_LOGI(TAG, "%s fw %s (IDF %s) on %s rev %u, %u MB flash, %u MB PSRAM, reset reason %u", s_identity.hwid,
             s_identity.fwVersion, s_identity.idf, chipName(s_identity.chipModel), s_identity.chipRev,
             s_identity.flashMb, s_identity.psramMb, s_identity.resetReason);

    // 4. Provisioning over the serial console - available with or without Wi-Fi.
    prov::BoardStatic board;
    snprintf(board.hwid, sizeof board.hwid, "%s", s_identity.hwid);
    snprintf(board.fw, sizeof board.fw, "%s", s_identity.fwVersion);
    snprintf(board.chip, sizeof board.chip, "%s", chipName(s_identity.chipModel));
    board.chipRev = s_identity.chipRev;
    board.flashMb = s_identity.flashMb;
    snprintf(board.mac, sizeof board.mac, "%.2s:%.2s:%.2s:%.2s:%.2s:%.2s", s_identity.hwid + 6, s_identity.hwid + 8,
             s_identity.hwid + 10, s_identity.hwid + 12, s_identity.hwid + 14, s_identity.hwid + 16);
    s_prov.begin(s_cfg, s_provBackend, board, &prov::Console::writeFn, &console);

    // 5. The MCP server.
    //    a) uplink pool: 8 x 2 KiB frames that results are encoded into directly;
    const bool psram = s_identity.psramMb > 0;
    if (!s_uplink.begin(CONFIG_IONITY_UPLINK_SLOTS, 2048, psram)) {
        ESP_LOGE(TAG, "uplink pool allocation failed");
    }
    //    b) tools: EdgeTools::begin() registers read_telemetry (0x02), get_device_info
    //       (0x01) and - with a LED configured - identify (0x06), plus the telemetry tick;
    static tools::EspTelemetrySource::Hooks hooks;
    hooks.net = &app::netStats;
    hooks.runUsMax = &app::taskRunUsMax;
    s_probes.begin(s_identity, hooks, CONFIG_IONITY_ADC_GPIO);
    tools::EdgeToolsConfig ec;
    ec.identity = &s_identity;
    ec.source = &s_probes;
    ec.indicator = s_led.begin(CONFIG_IONITY_LED_GPIO, kLedActiveHigh) ? &s_led : nullptr;
    ec.uplink = &s_uplink;
    ec.periodicSections = CONFIG_IONITY_TELEMETRY_SECTIONS;
    ec.linkUp = [](void*) { return net::MqttLink::instance().connected(); };
    ec.initialMode = node.mode;
    if (!s_edge.begin(ec, s_server)) ESP_LOGE(TAG, "tool registration failed");
    //    c) job pool (CONFIG_IONITY_JOB_SLOTS x 4 KiB) and mcp_worker, pinned to the tool core.
    mcp::ServerConfig sc;
    sc.slots = CONFIG_IONITY_JOB_SLOTS;
    sc.slotSize = EWP_LIMIT_DOWNLINK_FRAME;
    sc.preferPsram = psram;
    sc.workerStackBytes = kWorkerStack;
    sc.workerPriority = kWorkerPrio;
    sc.workerCore = kToolCore;
    if (!s_server.begin(sc, s_uplink)) ESP_LOGE(TAG, "MCP server start failed");

    // 6. Network: Wi-Fi station + the EWP/1 MQTT link. net_task (the only publisher)
    //    runs on the network core; esp-mqtt's task is pinned there by sdkconfig.
    app::Wiring wiring;
    wiring.cfg = &s_cfg;
    wiring.server = &s_server;
    wiring.uplink = &s_uplink;
    wiring.edge = &s_edge;
    wiring.prov = &s_prov;
    wiring.hwid = s_identity.hwid;
    wiring.autoStartLink = node.provisioned() && !node.standalone();
    app::wire(wiring);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    if (!net::WifiManager::instance().begin(s_identity.hwid, kWifiPowerSave)) {
        ESP_LOGE(TAG, "Wi-Fi init failed");
    }
    net::MqttLink& link = net::MqttLink::instance();
    link.begin(s_server, s_uplink, s_edge);
    link.setSupervisor(&app::superviseLink, nullptr);
    link.startTask(kNetPrio, kNetCore, kNetStack);

    // 7. Provisioning tasks, then the boot announcement (obligation 1).
    s_provBackend.begin(kProvPrio, kProvStack);
    console.startTask(s_prov, kConsolePrio, kNetCore, kConsoleStack);
    s_prov.announce();

    if (node.provisioned()) {
        app::connectWifi(node);
    } else {
        ESP_LOGW(TAG, "not provisioned - waiting for the Ionity portal on this serial port (%s)",
                 console.transportName());
    }

    // 8. Periodic TELEMETRY: the timer only posts a tick; collection runs on Core 1.
    if (CONFIG_IONITY_TELEMETRY_PERIOD_S > 0) {
        esp_timer_create_args_t ta = {};
        ta.callback = &onTelemetryTimer;
        ta.dispatch_method = ESP_TIMER_TASK;
        ta.name = "telemetry";
        if (esp_timer_create(&ta, &s_telemetryTimer) == ESP_OK) {
            esp_timer_start_periodic(s_telemetryTimer, static_cast<uint64_t>(CONFIG_IONITY_TELEMETRY_PERIOD_S) * 1000000u);
        }
    }
    ESP_LOGI(TAG, "ready: %u tools, %u job slots, worker on core %d", static_cast<unsigned>(s_server.toolCount()),
             static_cast<unsigned>(s_server.slots()), kToolCore);
    // app_main returns; the main task is deleted. Everything runs in the tasks above.
}
