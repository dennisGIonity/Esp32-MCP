/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | net/MqttLink.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The EWP/1 link over MQTT 5 (esp-mqtt) and net_task, the single publisher.
 *
 *   topics      ionity/ewp1/<site>/<hwid>/{dn,rs,tm,ev,hello,st} + ionity/ewp1/_all/dn
 *   session     clean start, session expiry 0: the broker never queues a stale
 *               command for an offline board (no "relay ON" replayed an hour later)
 *   last will   STATUS{state: OFFLINE} on `st`, retained, QoS 1
 *   on connect  subscribe dn + _all/dn (QoS 1), then HELLO (retained) and STATUS ONLINE (retained)
 *   receive     MQTT task (Core 0) -> MCPServer::onDownlink(): header checks + memcpy + 0-tick enqueue
 *   transmit    net_task (Core 0) -> Uplink::nextDue() -> esp_mqtt_client_enqueue() (outbox, no socket I/O)
 *   limits      RX buffer = downlink limit + 512 (topic/properties); MQTT 5 Maximum Packet Size
 *               tells the broker not to send anything bigger; a fragmented message is refused
 *               with ERR TOO_LARGE instead of being reassembled on the heap
 *
 * THREADS: client_ is created/destroyed under clientMu_, which net_task also
 * holds while it enqueues - the provisioning worker can restart the link
 * (`test` with new settings) without a use-after-free in the publisher.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <atomic>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "mcp/MCPServer.h"
#include "mcp/Uplink.h"
#include "mcp/os.h"
#include "mqtt_client.h"
#include "tools/EdgeTools.h"

namespace net {

struct LinkConfig {
    char uri[168] = {};      // mqtt://host:port or mqtts://host:port (canonical host_url)
    char user[65] = {};
    char pass[129] = {};
    const char* caPem = nullptr;  // NUL-terminated, must outlive the client; nullptr = certificate bundle
    char site[33] = {};
    char hwid[24] = {};
};

class MqttLink {
public:
    static MqttLink& instance();

    /** Boot: event group, mutex. The link is started later with start(). */
    bool begin(mcp::MCPServer& server, mcp::Uplink& uplink, tools::EdgeTools& edge);

    /** Create and start the client (stops a running one first). Not from the MQTT task. */
    bool start(const LinkConfig& cfg);
    /** Stop and destroy the client. Not from the MQTT task. */
    void stop();
    bool started() const;

    bool connected() const { return connected_.load(std::memory_order_acquire); }
    uint32_t txFail() const { return txFail_.load(std::memory_order_relaxed); }
    uint16_t reconnects() const { return static_cast<uint16_t>(reconnects_.load(std::memory_order_relaxed)); }
    uint32_t droppedOffline() const { return droppedOffline_.load(std::memory_order_relaxed); }

    /**
     * Graceful restart: publish STATUS{REBOOTING} (retained, replaces what the
     * Last Will would say) and give the MQTT task up to waitMs to send it.
     */
    void announceReboot(uint32_t waitMs);

    /** Wait for CONNECTED or a definitive error. detail gets a short human reason. BLOCKS. */
    bool waitConnected(uint32_t timeoutMs, char* detail, size_t cap);

    /** Start net_task (the publisher). */
    bool startTask(unsigned priority, int core, uint32_t stackBytes);
    uint32_t runUsMax() const { return runUsMax_.load(std::memory_order_relaxed); }

    /** Hook called by net_task every loop (link supervision: start on first Wi-Fi address, SNTP). */
    using Supervisor = void (*)(void* user);
    void setSupervisor(Supervisor fn, void* user) {
        supervisor_ = fn;
        supervisorUser_ = user;
    }

private:
    MqttLink() = default;
    static void onEvent(void* arg, esp_event_base_t base, int32_t id, void* data);
    static void taskEntry(void* self);
    void taskLoop();
    void publishPresence();
    bool publishFrame(const uint8_t* frame, size_t len, ewp::Channel ch);  // clientMu_ held
    const char* topicFor(ewp::Channel ch) const;

    mcp::MCPServer* server_ = nullptr;
    mcp::Uplink* uplink_ = nullptr;
    tools::EdgeTools* edge_ = nullptr;
    EventGroupHandle_t ev_ = nullptr;
    mutable os::Mutex clientMu_;
    esp_mqtt_client_handle_t client_ = nullptr;
    LinkConfig cfg_;

    char tDn_[80] = {};
    char tRs_[80] = {};
    char tTm_[80] = {};
    char tEv_[80] = {};
    char tHello_[80] = {};
    char tSt_[80] = {};
    uint8_t will_[32] = {};
    size_t willLen_ = 0;
    // Last MQTT_EVENT_ERROR as numbers (atomics: written by the MQTT task, read by the
    // provisioning worker); formatted into text only by the reader.
    std::atomic<uint32_t> errKind_{0};  // 0 none, 1 transport (TCP/TLS), 2 refused by the broker
    std::atomic<int32_t> errCode_{0};

    std::atomic<bool> connected_{false};
    std::atomic<bool> everConnected_{false};
    std::atomic<uint32_t> txFail_{0};
    std::atomic<uint32_t> reconnects_{0};
    std::atomic<uint32_t> droppedOffline_{0};
    std::atomic<uint32_t> runUsMax_{0};
    Supervisor supervisor_ = nullptr;
    void* supervisorUser_ = nullptr;
};

}  // namespace net
