/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/EspNetPlatform.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "netobserve/EspNetPlatform.h"

#include <string.h>

#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_net_stack.h"
#include "lwip/etharp.h"
#include "lwip/inet.h"
#include "lwip/netif.h"
#include "nvs.h"

namespace netobs {
namespace {

const char* const TAG = "netobs";
constexpr const char* kNs = "ionity";
constexpr const char* kKey = "watchlist";

esp_netif_t* sta() { return esp_netif_get_handle_from_ifkey("WIFI_STA_DEF"); }

struct LookupCtx {
    esp_netif_t* nif;
    uint32_t ip;
    uint8_t mac[6];
    bool ok;
};

struct TableCtx {
    ArpEntry* out;
    size_t max;
    size_t n;
};

struct RequestCtx {
    esp_netif_t* nif;
    const uint32_t* ips;
    size_t n;
    size_t sent;
};

/* These run inside the TCP/IP task (esp_netif_tcpip_exec): lwIP's ARP table is not thread-safe. */

esp_err_t lookupInTcpip(void* p) {
    LookupCtx* c = static_cast<LookupCtx*>(p);
    struct netif* n = static_cast<struct netif*>(esp_netif_get_netif_impl(c->nif));
    if (n == nullptr) return ESP_FAIL;
    ip4_addr_t a;
    a.addr = lwip_htonl(c->ip);
    struct eth_addr* eth = nullptr;
    const ip4_addr_t* ipr = nullptr;
    if (etharp_find_addr(n, &a, &eth, &ipr) >= 0 && eth != nullptr) {
        memcpy(c->mac, eth->addr, 6);
        c->ok = true;
    }
    return ESP_OK;
}

esp_err_t tableInTcpip(void* p) {
    TableCtx* c = static_cast<TableCtx*>(p);
    for (size_t i = 0; i < ARP_TABLE_SIZE && c->n < c->max; ++i) {
        ip4_addr_t* ip = nullptr;
        struct netif* nif = nullptr;
        struct eth_addr* eth = nullptr;
        if (etharp_get_entry(i, &ip, &nif, &eth) == 1 && ip != nullptr && eth != nullptr) {
            c->out[c->n].ip = lwip_ntohl(ip->addr);
            memcpy(c->out[c->n].mac, eth->addr, 6);
            ++c->n;
        }
    }
    return ESP_OK;
}

esp_err_t requestInTcpip(void* p) {
    RequestCtx* c = static_cast<RequestCtx*>(p);
    struct netif* n = static_cast<struct netif*>(esp_netif_get_netif_impl(c->nif));
    if (n == nullptr) return ESP_FAIL;
    for (size_t i = 0; i < c->n; ++i) {
        ip4_addr_t a;
        a.addr = lwip_htonl(c->ips[i]);
        if (etharp_request(n, &a) == ERR_OK) ++c->sent;
    }
    return ESP_OK;
}

}  // namespace

bool EspNetPlatform::stationIp(uint32_t& ip, uint32_t& mask) {
    esp_netif_t* n = sta();
    esp_netif_ip_info_t info;
    if (n == nullptr || esp_netif_get_ip_info(n, &info) != ESP_OK || info.ip.addr == 0) return false;
    ip = lwip_ntohl(info.ip.addr);
    mask = lwip_ntohl(info.netmask.addr);
    return true;
}

bool EspNetPlatform::arpLookup(uint32_t ip, uint8_t mac[6]) {
    LookupCtx c{sta(), ip, {}, false};
    if (c.nif == nullptr || esp_netif_tcpip_exec(&lookupInTcpip, &c) != ESP_OK || !c.ok) return false;
    memcpy(mac, c.mac, 6);
    return true;
}

size_t EspNetPlatform::arpTable(ArpEntry* out, size_t max) {
    TableCtx c{out, max, 0};
    if (esp_netif_tcpip_exec(&tableInTcpip, &c) != ESP_OK) return 0;
    return c.n;
}

size_t EspNetPlatform::arpRequest(const uint32_t* ips, size_t n) {
    RequestCtx c{sta(), ips, n, 0};
    if (c.nif == nullptr || esp_netif_tcpip_exec(&requestInTcpip, &c) != ESP_OK) return 0;
    return c.sent;
}

void EspNetPlatform::useResolvers(const uint32_t* ips, size_t n) {
    esp_netif_t* nif = sta();
    if (nif == nullptr || n == 0) return;
    // The router hands out THIS node as the LAN's DNS server, so the node's own
    // DHCP lease names itself too. Its own lookups (SNTP, the MQTT host name) go
    // to the upstreams instead - never in a circle through its own forwarder.
    const esp_netif_dns_type_t types[2] = {ESP_NETIF_DNS_MAIN, ESP_NETIF_DNS_BACKUP};
    for (size_t i = 0; i < n && i < 2; ++i) {
        esp_netif_dns_info_t d = {};
        d.ip.type = ESP_IPADDR_TYPE_V4;
        d.ip.u_addr.ip4.addr = lwip_htonl(ips[i]);
        esp_netif_set_dns_info(nif, types[i], &d);
    }
}

bool EspNetPlatform::loadWatchlist(uint8_t* buf, size_t cap, size_t& len) {
    nvs_handle_t h;
    if (nvs_open(kNs, NVS_READONLY, &h) != ESP_OK) return false;
    size_t n = cap;
    const esp_err_t e = nvs_get_blob(h, kKey, buf, &n);
    nvs_close(h);
    if (e != ESP_OK) return false;
    len = n;
    return true;
}

bool EspNetPlatform::saveWatchlist(const uint8_t* buf, size_t len) {
    nvs_handle_t h;
    if (nvs_open(kNs, NVS_READWRITE, &h) != ESP_OK) return false;
    esp_err_t e = nvs_set_blob(h, kKey, buf, len);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    if (e != ESP_OK) ESP_LOGW(TAG, "watchlist not saved: %s", esp_err_to_name(e));
    return e == ESP_OK;
}

}  // namespace netobs
