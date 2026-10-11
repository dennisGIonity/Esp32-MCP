/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | net/WifiManager.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Station-mode Wi-Fi with reconnect back-off, scan and a bounded "join and
 * wait" for the provisioning `test` op.
 *
 *  - Credentials live in NVS namespace "ionity" (prov::ConfigStore). The
 *    driver's own copy is disabled (WIFI_STORAGE_RAM), so there is exactly
 *    one source of truth; factory_reset still calls esp_wifi_restore() to
 *    wipe what fw 2.x (Arduino, persistent mode) left in the driver's NVS.
 *  - Event callbacks run in the default event-loop task; they only update
 *    atomics/event bits and arm a timer - never block there.
 *  - Reconnect: 1 s, 2 s, 4 s ... 30 s, reset when an address is obtained.
 *  - waitUp()/scan() BLOCK: call them from the provisioning worker only,
 *    never from net_task or the MQTT task.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <atomic>

#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

namespace net {

struct ApInfo {
    char ssid[33];
    uint8_t ssidLen;
    int8_t rssi;
    uint8_t channel;
    bool open;
};

class WifiManager {
public:
    static WifiManager& instance();

    /** netif + driver init, STA mode started, no connection yet. Boot only. */
    bool begin(const char* hostname, bool powerSave);

    /** (Re)configure and connect. Empty ssid = stay disconnected. Asynchronous. */
    void connect(const char* ssid, const char* pass);

    bool up() const { return ip_.load(std::memory_order_acquire) != 0; }
    /** IPv4 in network order bytes; false while down. */
    bool ipv4(uint8_t out[4]) const;
    void ipString(char* out, size_t cap) const;
    bool apInfo(int8_t& rssi, uint8_t& channel) const;
    uint8_t lastReason() const { return lastReason_.load(std::memory_order_relaxed); }
    uint16_t reconnects() const { return static_cast<uint16_t>(reconnects_.load(std::memory_order_relaxed)); }
    /** Increments on every new address: net_task uses it to (re)start what depends on the link. */
    uint32_t upGeneration() const { return upGen_.load(std::memory_order_acquire); }

    /** Human text for a disconnect reason (fw 2.x wording, which the portal's hints match). */
    static const char* reasonText(uint8_t reason);
    /** Reasons that will not fix themselves by retrying (wrong password, rejected). */
    static bool isAuthFailure(uint8_t reason);

    /**
     * Wait for an address. Returns 1 = up, 0 = timeout, -1 = definitive
     * authentication failure (only when failFastOnAuth). BLOCKS.
     */
    int waitUp(uint32_t timeoutMs, bool failFastOnAuth);

    /** Active scan; returns the number of APs written to out (<= max), -1 on failure. BLOCKS. */
    int scan(ApInfo* out, int max, uint32_t timeoutMs);

    /** Forget the driver's stored configuration (factory reset). */
    bool restoreDefaults();

private:
    WifiManager() = default;
    static void onEvent(void* arg, esp_event_base_t base, int32_t id, void* data);
    static void onRetry(void* arg);
    void scheduleRetry();

    EventGroupHandle_t ev_ = nullptr;
    esp_timer_handle_t retry_ = nullptr;
    esp_netif_t* netif_ = nullptr;
    std::atomic<bool> haveCreds_{false};
    std::atomic<bool> scanning_{false};
    std::atomic<int64_t> ignoreLeaveUntilUs_{0};
    std::atomic<uint8_t> lastReason_{0};
    std::atomic<uint32_t> ip_{0};
    std::atomic<uint32_t> reconnects_{0};
    std::atomic<uint32_t> backoffMs_{1000};
    std::atomic<uint32_t> upGen_{0};
    std::atomic<bool> wasUp_{false};
};

}  // namespace net
