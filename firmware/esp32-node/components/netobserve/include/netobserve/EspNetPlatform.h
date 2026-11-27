/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/EspNetPlatform.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * NetObserve on ESP-IDF: the station address (esp_netif), lwIP's ARP cache
 * (read and ARP requests inside the TCP/IP task via esp_netif_tcpip_exec),
 * the node's own resolver (esp_netif DNS info), the watchlist blob in NVS
 * (namespace "ionity", key "watchlist" - erased by factory_reset with the
 * rest of the settings). Link state and the alert output are hooks into the
 * application (MQTT link, actuator bank). ping_host uses lwIP's esp_ping
 * (ping/ping_sock.h): the session runs in its own small task and the
 * callbacks only update counters, so the MCP worker just polls them.
 */
#pragma once

#include "netobserve/NetObserve.h"

namespace netobs {

class EspNetPlatform final : public Platform {
public:
    struct Hooks {
        bool (*linkUp)(void* user) = nullptr;
        void (*alert)(void* user, uint8_t category, uint32_t holdMs) = nullptr;
        void* user = nullptr;
    };
    void begin(const Hooks& hooks) { hooks_ = hooks; }

    bool stationIp(uint32_t& ip, uint32_t& mask) override;
    bool arpLookup(uint32_t ip, uint8_t mac[6]) override;
    size_t arpTable(ArpEntry* out, size_t max) override;
    size_t arpRequest(const uint32_t* ips, size_t n) override;
    void useResolvers(const uint32_t* ips, size_t n) override;
    bool linkUp() override { return hooks_.linkUp != nullptr && hooks_.linkUp(hooks_.user); }
    void alert(uint8_t category, uint32_t holdMs) override {
        if (hooks_.alert != nullptr) hooks_.alert(hooks_.user, category, holdMs);
    }
    bool loadWatchlist(uint8_t* buf, size_t cap, size_t& len) override;
    bool saveWatchlist(const uint8_t* buf, size_t len) override;
    bool resolveHost(const char* host, uint32_t& ip) override;
    bool pingStart(uint32_t ip, uint8_t count, uint16_t timeoutMs, uint16_t intervalMs) override;
    void pingStatus(PingStats& out) override;
    void pingStop() override;

private:
    Hooks hooks_;
    void* ping_ = nullptr;  // esp_ping_handle_t
};

}  // namespace netobs
