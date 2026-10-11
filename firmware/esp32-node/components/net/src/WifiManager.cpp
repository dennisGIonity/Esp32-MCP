/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | net/WifiManager.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "net/WifiManager.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_wifi.h"
#include "lwip/ip4_addr.h"

namespace net {
namespace {

const char* const TAG = "wifi";
constexpr EventBits_t kUp = BIT0;
constexpr EventBits_t kFail = BIT1;
constexpr EventBits_t kScanDone = BIT2;
constexpr uint32_t kBackoffMaxMs = 30000;

/** Scan results: provisioning worker only (scan() is never re-entered). */
constexpr uint16_t kMaxRecords = 24;
wifi_ap_record_t s_records[kMaxRecords];

TickType_t ticks(uint32_t ms) { return pdMS_TO_TICKS(ms) == 0 ? 1 : pdMS_TO_TICKS(ms); }

}  // namespace

WifiManager& WifiManager::instance() {
    static WifiManager w;
    return w;
}

const char* WifiManager::reasonText(uint8_t r) {
    switch (r) {
        case 0: return "";
        case WIFI_REASON_AUTH_EXPIRE:
        case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
        case WIFI_REASON_HANDSHAKE_TIMEOUT: return "wrong password (auth/4-way handshake failed)";
        case WIFI_REASON_NO_AP_FOUND:
            return "SSID not found (off, hidden on another band, or 5 GHz only - ESP32 is 2.4 GHz)";
        case WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY:
        case WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD:
            return "security mode not supported (use WPA2-PSK or WPA2/WPA3; not WEP, not WPA1)";
        case WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD: return "SSID found but the signal is too weak";
        case WIFI_REASON_AUTH_FAIL: return "authentication failed (password rejected)";
        case WIFI_REASON_ASSOC_FAIL: return "association failed (AP full or MAC filtered)";
        case WIFI_REASON_CONNECTION_FAIL: return "connection failed";
        case WIFI_REASON_802_1X_AUTH_FAILED: return "802.1X / enterprise auth failed";
        case WIFI_REASON_ASSOC_LEAVE: return "left the network";
        case WIFI_REASON_BEACON_TIMEOUT: return "beacon timeout (weak signal)";
        default: return "disconnected";
    }
}

bool WifiManager::isAuthFailure(uint8_t r) {
    return r == WIFI_REASON_AUTH_EXPIRE || r == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT ||
           r == WIFI_REASON_HANDSHAKE_TIMEOUT || r == WIFI_REASON_AUTH_FAIL || r == WIFI_REASON_802_1X_AUTH_FAILED ||
           r == WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY || r == WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD;
}

bool WifiManager::begin(const char* hostname, bool powerSave) {
    ev_ = xEventGroupCreate();
    if (ev_ == nullptr) return false;
    // esp_netif_init() and esp_event_loop_create_default() are done once by the app.
    netif_ = esp_netif_create_default_wifi_sta();
    if (netif_ == nullptr) return false;
    if (hostname != nullptr) esp_netif_set_hostname(netif_, hostname);  // shows up in the router's DHCP list

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    if (esp_wifi_init(&init) != ESP_OK) return false;
    esp_wifi_set_storage(WIFI_STORAGE_RAM);  // one source of truth: NVS "ionity"

    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &WifiManager::onEvent, this, nullptr);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &WifiManager::onEvent, this, nullptr);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_LOST_IP, &WifiManager::onEvent, this, nullptr);

    esp_timer_create_args_t t = {};
    t.callback = &WifiManager::onRetry;
    t.arg = this;
    t.dispatch_method = ESP_TIMER_TASK;
    t.name = "wifi_retry";
    if (esp_timer_create(&t, &retry_) != ESP_OK) return false;

    if (esp_wifi_set_mode(WIFI_MODE_STA) != ESP_OK || esp_wifi_start() != ESP_OK) return false;
    // Modem sleep adds up to a DTIM interval (100-300 ms) to every downlink frame.
    // Mains-powered nodes answer MCP calls faster without it.
    esp_wifi_set_ps(powerSave ? WIFI_PS_MIN_MODEM : WIFI_PS_NONE);
    return true;
}

void WifiManager::connect(const char* ssid, const char* pass) {
    wifi_config_t cfg = {};
    const size_t sl = strnlen(ssid, sizeof cfg.sta.ssid);
    const size_t pl = strnlen(pass, sizeof cfg.sta.password);
    memcpy(cfg.sta.ssid, ssid, sl);  // 32 bytes, NOT necessarily NUL-terminated (802.11)
    memcpy(cfg.sta.password, pass, pl);
    cfg.sta.threshold.authmode = pl > 0 ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;  // WPA3 is "more secure": allowed
    cfg.sta.pmf_cfg.capable = true;
    cfg.sta.pmf_cfg.required = false;
    cfg.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    cfg.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;  // strongest AP of a mesh, not the first heard
    cfg.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    cfg.sta.failure_retry_cnt = 2;

    esp_timer_stop(retry_);
    haveCreds_.store(sl > 0, std::memory_order_release);
    // Our own disconnect produces an ASSOC_LEAVE event: do not count it as a failure.
    ignoreLeaveUntilUs_.store(esp_timer_get_time() + 1000000, std::memory_order_release);
    esp_wifi_disconnect();
    ip_.store(0, std::memory_order_release);
    xEventGroupClearBits(ev_, kUp | kFail);
    lastReason_.store(0, std::memory_order_relaxed);
    backoffMs_.store(1000, std::memory_order_relaxed);
    if (esp_wifi_set_config(WIFI_IF_STA, &cfg) != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_set_config failed");
        return;
    }
    if (sl > 0) {
        ESP_LOGI(TAG, "joining \"%.*s\"", static_cast<int>(sl), ssid);  // never the password
        esp_wifi_connect();
    }
}

void WifiManager::scheduleRetry() {
    if (!haveCreds_.load(std::memory_order_acquire) || scanning_.load(std::memory_order_acquire)) return;
    const uint32_t ms = backoffMs_.load(std::memory_order_relaxed);
    esp_timer_stop(retry_);
    esp_timer_start_once(retry_, static_cast<uint64_t>(ms) * 1000u);
    backoffMs_.store(ms * 2 > kBackoffMaxMs ? kBackoffMaxMs : ms * 2, std::memory_order_relaxed);
}

void WifiManager::onRetry(void* arg) {
    WifiManager* self = static_cast<WifiManager*>(arg);
    if (self->haveCreds_.load(std::memory_order_acquire) && !self->scanning_.load(std::memory_order_acquire) &&
        !self->up()) {
        esp_wifi_connect();
    }
}

void WifiManager::onEvent(void* arg, esp_event_base_t base, int32_t id, void* data) {
    WifiManager* self = static_cast<WifiManager*>(arg);
    if (base == WIFI_EVENT) {
        switch (id) {
            case WIFI_EVENT_STA_START:
                if (self->haveCreds_.load(std::memory_order_acquire)) esp_wifi_connect();
                break;
            case WIFI_EVENT_STA_DISCONNECTED: {
                const auto* d = static_cast<const wifi_event_sta_disconnected_t*>(data);
                self->ip_.store(0, std::memory_order_release);
                xEventGroupClearBits(self->ev_, kUp);
                if (d->reason == WIFI_REASON_ASSOC_LEAVE &&
                    esp_timer_get_time() < self->ignoreLeaveUntilUs_.load(std::memory_order_acquire)) {
                    break;  // the disconnect we asked for in connect()
                }
                self->lastReason_.store(d->reason, std::memory_order_relaxed);
                if (self->wasUp_.exchange(false)) self->reconnects_.fetch_add(1, std::memory_order_relaxed);
                xEventGroupSetBits(self->ev_, kFail);
                ESP_LOGW(TAG, "disconnected: %s (reason %u)", reasonText(d->reason), static_cast<unsigned>(d->reason));
                self->scheduleRetry();
                break;
            }
            case WIFI_EVENT_SCAN_DONE: xEventGroupSetBits(self->ev_, kScanDone); break;
            default: break;
        }
    } else if (base == IP_EVENT) {
        if (id == IP_EVENT_STA_GOT_IP) {
            const auto* g = static_cast<const ip_event_got_ip_t*>(data);
            self->ip_.store(g->ip_info.ip.addr, std::memory_order_release);
            self->lastReason_.store(0, std::memory_order_relaxed);
            self->backoffMs_.store(1000, std::memory_order_relaxed);
            self->wasUp_.store(true);
            self->upGen_.fetch_add(1, std::memory_order_acq_rel);
            xEventGroupClearBits(self->ev_, kFail);
            xEventGroupSetBits(self->ev_, kUp);
            ESP_LOGI(TAG, "up, ip " IPSTR, IP2STR(&g->ip_info.ip));
        } else if (id == IP_EVENT_STA_LOST_IP) {
            self->ip_.store(0, std::memory_order_release);
            xEventGroupClearBits(self->ev_, kUp);
        }
    }
}

bool WifiManager::ipv4(uint8_t out[4]) const {
    const uint32_t a = ip_.load(std::memory_order_acquire);
    if (a == 0) return false;
    memcpy(out, &a, 4);  // lwIP keeps the address in network byte order
    return true;
}

void WifiManager::ipString(char* out, size_t cap) const {
    uint8_t b[4];
    if (!ipv4(b)) {
        if (cap > 0) out[0] = '\0';
        return;
    }
    snprintf(out, cap, "%u.%u.%u.%u", b[0], b[1], b[2], b[3]);
}

bool WifiManager::apInfo(int8_t& rssi, uint8_t& channel) const {
    if (!up()) return false;
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) return false;
    rssi = ap.rssi;
    channel = ap.primary;
    return true;
}

int WifiManager::waitUp(uint32_t timeoutMs, bool failFastOnAuth) {
    const int64_t until = esp_timer_get_time() + static_cast<int64_t>(timeoutMs) * 1000;
    for (;;) {
        if (up()) return 1;
        const int64_t left = until - esp_timer_get_time();
        if (left <= 0) return 0;
        const uint32_t ms = left / 1000 > 500 ? 500 : static_cast<uint32_t>(left / 1000) + 1;
        const EventBits_t b = xEventGroupWaitBits(ev_, kUp | kFail, pdFALSE, pdFALSE, ticks(ms));
        if (b & kUp) return 1;
        if (b & kFail) {
            xEventGroupClearBits(ev_, kFail);
            if (failFastOnAuth && isAuthFailure(lastReason())) return -1;
        }
    }
}

int WifiManager::scan(ApInfo* out, int max, uint32_t timeoutMs) {
    scanning_.store(true, std::memory_order_release);
    esp_timer_stop(retry_);
    if (!up()) {
        // A scan is refused while the station is still trying to associate (fw 2.x lesson).
        ignoreLeaveUntilUs_.store(esp_timer_get_time() + 1000000, std::memory_order_release);
        esp_wifi_disconnect();
    }
    xEventGroupClearBits(ev_, kScanDone);
    wifi_scan_config_t sc = {};
    sc.show_hidden = false;
    sc.scan_type = WIFI_SCAN_TYPE_ACTIVE;
    int n = -1;
    if (esp_wifi_scan_start(&sc, false) == ESP_OK &&
        (xEventGroupWaitBits(ev_, kScanDone, pdTRUE, pdFALSE, ticks(timeoutMs)) & kScanDone)) {
        uint16_t count = kMaxRecords;
        if (esp_wifi_scan_get_ap_records(&count, s_records) == ESP_OK) {
            n = 0;
            for (uint16_t i = 0; i < count && n < max; ++i) {
                ApInfo& a = out[n++];
                a.ssidLen = static_cast<uint8_t>(strnlen(reinterpret_cast<const char*>(s_records[i].ssid), 32));
                memcpy(a.ssid, s_records[i].ssid, a.ssidLen);
                a.ssid[a.ssidLen] = '\0';
                a.rssi = s_records[i].rssi;
                a.channel = s_records[i].primary;
                a.open = s_records[i].authmode == WIFI_AUTH_OPEN;
            }
        }
    } else {
        esp_wifi_scan_stop();
        esp_wifi_clear_ap_list();
    }
    scanning_.store(false, std::memory_order_release);
    if (haveCreds_.load(std::memory_order_acquire) && !up()) esp_wifi_connect();  // resume joining
    return n;
}

bool WifiManager::restoreDefaults() { return esp_wifi_restore() == ESP_OK; }

}  // namespace net
