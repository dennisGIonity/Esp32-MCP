/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | main/NodeApp.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "NodeApp.h"

#include <stdio.h>
#include <string.h>

#include <initializer_list>

#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "ewp/crc32.h"
#include "mbedtls/x509_crt.h"
#include "net/MqttLink.h"
#include "net/WifiManager.h"
#include "prov/Console.h"
#include "sdkconfig.h"

namespace app {
namespace {

const char* const TAG = "node";

Wiring g;
os::Mutex s_linkMu;  // serialises startLink(): stop old client -> reload CA -> start new client
EspProvBackend* s_backend = nullptr;

/** The CA the running MQTT client points at (esp-mqtt does not copy certificates). Guarded by s_linkMu. */
char s_ca[prov::Limits::kCaPem + 1];

/*
 * What is applied to the radio / the link, kept as CRC-32 fingerprints rather
 * than copies: one less place in RAM that holds the Wi-Fi and MQTT passwords.
 */
std::atomic<uint32_t> s_wifiKey{0};
std::atomic<uint32_t> s_linkKey{0};
bool s_sntpStarted = false;   // net_task only
uint32_t s_seenUpGen = 0;     // net_task only

uint32_t fingerprint(std::initializer_list<const char*> parts) {
    uint32_t crc = 0;
    for (const char* p : parts) {
        crc = ewp::crc32_update(crc, p, strlen(p));
        crc = ewp::crc32_update(crc, "\x1f", 1);  // separator: ("ab","c") != ("a","bc")
    }
    return crc | 1u;  // never 0 (= "nothing applied")
}

const char* hostUrlOrDefault(const prov::NodeConfig& c) {
    return c.hostUrl[0] != '\0' ? c.hostUrl : CONFIG_IONITY_DEFAULT_HOST_URL;
}

uint32_t linkFingerprint(const prov::NodeConfig& c) {
    return fingerprint({hostUrlOrDefault(c), c.mqttUser, c.mqttPass, c.site, c.hasCa ? "ca" : "-"});
}

}  // namespace

void wire(const Wiring& w) {
    g = w;
    s_linkMu.begin(false);
}

/* ---- applied settings ------------------------------------------------------- */

void connectWifi(const prov::NodeConfig& c) {
    s_wifiKey.store(fingerprint({c.ssid, c.pass}));
    net::WifiManager::instance().connect(c.ssid, c.pass);
}

bool wifiApplied(const prov::NodeConfig& c) { return s_wifiKey.load() == fingerprint({c.ssid, c.pass}); }

bool startLink(const prov::NodeConfig& c) {
    os::Lock lk(s_linkMu);
    net::MqttLink& link = net::MqttLink::instance();
    link.stop();  // after this nothing reads s_ca any more
    net::LinkConfig lc;
    snprintf(lc.uri, sizeof lc.uri, "%s", hostUrlOrDefault(c));
    snprintf(lc.user, sizeof lc.user, "%s", c.mqttUser);
    snprintf(lc.pass, sizeof lc.pass, "%s", c.mqttPass);
    snprintf(lc.site, sizeof lc.site, "%s", c.site);
    snprintf(lc.hwid, sizeof lc.hwid, "%s", g.hwid);
    lc.caPem = (c.hasCa && g.cfg->readCa(s_ca, sizeof s_ca)) ? s_ca : nullptr;
    const bool ok = link.start(lc);
    memset(lc.pass, 0, sizeof lc.pass);  // the link keeps its own copy
    s_linkKey.store(ok ? linkFingerprint(c) : 0);
    return ok;
}

bool linkApplied(const prov::NodeConfig& c) { return s_linkKey.load() == linkFingerprint(c); }

void superviseLink(void*) {
    // Runs inside net_task every <= 500 ms. Cheap unless the link just came up.
    net::WifiManager& wifi = net::WifiManager::instance();
    const uint32_t gen = wifi.upGeneration();
    if (gen == s_seenUpGen || !wifi.up()) return;
    s_seenUpGen = gen;
    if (!s_sntpStarted && CONFIG_IONITY_SNTP_SERVER[0] != '\0') {
        esp_sntp_config_t sc = ESP_NETIF_SNTP_DEFAULT_CONFIG(CONFIG_IONITY_SNTP_SERVER);
        s_sntpStarted = esp_netif_sntp_init(&sc) == ESP_OK;  // TELEMETRY.unixS appears once synced
    }
    if (g.autoStartLink && !net::MqttLink::instance().started()) {
        prov::NodeConfig c;
        g.cfg->snapshot(c);
        if (c.provisioned() && !c.standalone()) startLink(c);
    }
}

/* ---- telemetry hooks ---------------------------------------------------------- */

void netStats(void*, tools::NetStats& o) {
    net::WifiManager& wifi = net::WifiManager::instance();
    net::MqttLink& link = net::MqttLink::instance();
    int8_t rssi = 0;
    uint8_t ch = 0;
    if (wifi.apInfo(rssi, ch)) {
        o.associated = true;
        o.rssiDbm = rssi;
        o.channel = ch;
    }
    // Frames that did not reach the broker: enqueue refused, dropped while offline, or no pool slot.
    o.txFail = link.txFail() + link.droppedOffline() + g.uplink->dropped();
    o.reconnects = link.reconnects();
    const size_t q = g.uplink->queued();
    o.uplinkQueued = static_cast<uint8_t>(q > 255 ? 255 : q);
}

uint32_t taskRunUsMax(void*, uint8_t id) {
    switch (id) {
        case kTaskNet: return net::MqttLink::instance().runUsMax();
        case kTaskWorker: return g.server->workerRunUsMax();
        case kTaskConsole: return prov::Console::instance().runUsMax();
        case kTaskProv: return s_backend != nullptr ? s_backend->runUsMax() : 0;
        default: return 0;  // tasks we do not own: stack high-water mark only
    }
}

bool x509Check(const char* pem, size_t len) {
    mbedtls_x509_crt crt;
    mbedtls_x509_crt_init(&crt);
    // PEM input: the length must include the terminating NUL.
    const int r = mbedtls_x509_crt_parse(&crt, reinterpret_cast<const unsigned char*>(pem), len + 1);
    mbedtls_x509_crt_free(&crt);
    return r == 0;
}

void restartNow() {
    ESP_LOGW(TAG, "restarting");
    net::MqttLink::instance().announceReboot(300);
    prov::Console::instance().flush(500);
    os::delayMs(150);  // obligation 9: reply, flush, wait >= 100 ms
    esp_restart();
}

/* ---- provisioning backend ---------------------------------------------------- */

bool EspProvBackend::begin(unsigned priority, uint32_t stackBytes) {
    s_backend = this;
    if (!jobs_.begin(2)) return false;
    return os::spawn("prov_worker", &EspProvBackend::taskEntry, this, stackBytes, priority, -1, nullptr);
}

void EspProvBackend::live(prov::LiveState& o) {
    net::WifiManager& wifi = net::WifiManager::instance();
    o.wifiUp = wifi.up();
    if (o.wifiUp) {
        wifi.ipString(o.ip, sizeof o.ip);
        int8_t rssi = 0;
        uint8_t ch = 0;
        if (wifi.apInfo(rssi, ch)) o.rssi = rssi;
    } else if (wifi.lastReason() != 0) {
        snprintf(o.wifiError, sizeof o.wifiError, "%s", net::WifiManager::reasonText(wifi.lastReason()));
    }
    o.mqttUp = net::MqttLink::instance().connected();
    o.mode = g.edge->mode();
}

bool EspProvBackend::startScan() {
    if (busy_.exchange(true)) return false;
    const Job j{1, 0};
    if (!jobs_.send(j, 0)) {
        busy_.store(false);
        return false;
    }
    return true;
}

bool EspProvBackend::startTest(uint32_t timeoutMs) {
    if (busy_.exchange(true)) return false;
    const Job j{2, timeoutMs};
    if (!jobs_.send(j, 0)) {
        busy_.store(false);
        return false;
    }
    return true;
}

void EspProvBackend::configChanged(uint32_t changed) {
    prov::NodeConfig c;
    g.cfg->snapshot(c);
    prov::Console::instance().setSecrets(c);  // new secrets are scrubbed from logs from now on
    if (changed & (prov::fieldBit(prov::Field::Ssid) | prov::fieldBit(prov::Field::Pass))) {
        connectWifi(c);  // Wi-Fi applies live; MQTT settings apply on `test` or at the next boot
    }
}

bool EspProvBackend::factoryResetWifi() { return net::WifiManager::instance().restoreDefaults(); }

void EspProvBackend::restart() { restartNow(); }

void EspProvBackend::taskEntry(void* self) {
    EspProvBackend* b = static_cast<EspProvBackend*>(self);
    for (;;) {
        Job j;
        if (!b->jobs_.receive(j, os::kForever)) continue;  // idle until asked; not on the TWDT
        const int64_t t0 = esp_timer_get_time();
        if (j.op == 1) b->runScan();
        else b->runTest(j.timeoutMs);
        b->busy_.store(false);
        const int64_t dt = esp_timer_get_time() - t0;
        const uint32_t us = dt > 0xFFFFFFFF ? 0xFFFFFFFFu : static_cast<uint32_t>(dt);
        if (us > b->runUsMax_.load()) b->runUsMax_.store(us);
    }
}

void EspProvBackend::runScan() {
    static net::ApInfo aps[prov::ScanOutcome::kMax];  // prov_worker only
    static prov::ScanOutcome r;
    r = prov::ScanOutcome{};
    const int n = net::WifiManager::instance().scan(aps, static_cast<int>(prov::ScanOutcome::kMax), 8000);
    r.ok = n >= 0;
    if (!r.ok) r.error = "scan failed (radio busy) - try again";
    for (int i = 0; i < n; ++i) {
        prov::ScanOutcome::Net& o = r.nets[r.count++];
        memcpy(o.ssid, aps[i].ssid, sizeof o.ssid);
        o.ssidLen = aps[i].ssidLen;
        o.rssi = aps[i].rssi;
        o.ch = aps[i].channel;
        o.open = aps[i].open;
    }
    g.prov->finishScan(r);
}

void EspProvBackend::runTest(uint32_t timeoutMs) {
    prov::NodeConfig c;
    g.cfg->snapshot(c);
    prov::TestOutcome r;
    const int64_t until = esp_timer_get_time() + static_cast<int64_t>(timeoutMs) * 1000;
    net::WifiManager& wifi = net::WifiManager::instance();

    // 1. Wi-Fi with the STORED settings (they may have been written seconds ago).
    if (!wifi.up() || !wifiApplied(c)) connectWifi(c);
    const int w = wifi.waitUp(timeoutMs, true);
    if (w == 1) {
        r.wifiOk = true;
        wifi.ipString(r.ip, sizeof r.ip);
        int8_t rssi = 0;
        uint8_t ch = 0;
        if (wifi.apInfo(rssi, ch)) r.rssi = rssi;
    } else {
        const uint8_t reason = wifi.lastReason();
        snprintf(r.wifiError, sizeof r.wifiError, "%s",
                 reason != 0 ? net::WifiManager::reasonText(reason) : "timeout joining Wi-Fi");
    }
    if (!r.wifiOk) {
        r.brokerReported = !c.standalone();
        snprintf(r.brokerDetail, sizeof r.brokerDetail, "no Wi-Fi");
        g.prov->finishTest(r);
        return;
    }
    if (c.standalone()) {
        snprintf(r.brokerDetail, sizeof r.brokerDetail, "standalone: no MCP host");
        g.prov->finishTest(r);
        return;
    }

    // 2. The MCP host (MQTT broker) with the stored settings, within what is left of the budget.
    int64_t leftMs = (until - esp_timer_get_time()) / 1000;
    if (leftMs < 3000) leftMs = 3000;
    if (leftMs > 10000) leftMs = 10000;
    net::MqttLink& link = net::MqttLink::instance();
    r.brokerReported = true;
    if (link.connected() && linkApplied(c)) {
        r.brokerOk = true;
        snprintf(r.brokerDetail, sizeof r.brokerDetail, "connected to %.80s", hostUrlOrDefault(c));
    } else if (!startLink(c)) {
        snprintf(r.brokerDetail, sizeof r.brokerDetail, "could not start the MQTT client (out of memory?)");
    } else {
        r.brokerOk = link.waitConnected(static_cast<uint32_t>(leftMs), r.brokerDetail, sizeof r.brokerDetail);
    }
    g.prov->finishTest(r);
}

}  // namespace app
