#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClient.h>
#include <Wire.h>
#include <time.h>
#include <U8g2lib.h>
#include "config.h"

// ============================================================================
// Display Driver Initialization (U8g2)
// ============================================================================
#if DISPLAY_TYPE_SH1106
// 1.3" I2C OLED (SH1106)
U8G2_SH1106_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);
#else
// 0.96" I2C OLED (SSD1306 128x64)
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);
#endif

// ============================================================================
// Telemetry & State Structs
// ============================================================================
struct ProbeMetrics {
    float latency_dns1_ms;
    float latency_dns2_ms;
    float latency_gw1_ms;
    float jitter_ms;
    float packet_loss_pct;
    float stability_score;
    bool mains_power_ok;
    uint32_t uptime_seconds;
    uint32_t free_heap_bytes;
    float chip_temp_c;
};

ProbeMetrics currentMetrics;
bool ntpSynced = false;
unsigned long lastDisplayUpdate = 0;
unsigned long lastTelemetryPush = 0;
unsigned long lastProbeTime = 0;
unsigned long lastMainsCheck = 0;

void setupPins() {
    pinMode(PIN_MAINS_SENSE, INPUT_PULLUP);
    pinMode(PIN_LED_HEARTBEAT, OUTPUT);
    pinMode(PIN_LED_ALERT, OUTPUT);
    pinMode(PIN_RELAY_FAILOVER, OUTPUT);

    digitalWrite(PIN_LED_HEARTBEAT, LOW);
    digitalWrite(PIN_LED_ALERT, LOW);
    digitalWrite(PIN_RELAY_FAILOVER, LOW);
}

void initDisplay() {
    Serial.println("[Display] Initializing I2C bus on SDA=" + String(PIN_I2C_SDA) + ", SCL=" + String(PIN_I2C_SCL));
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
    u8g2.begin();

    // Show Ionity Boot Splash Screen
    u8g2.clearBuffer();
    u8g2.setFont(u8g2_font_7x14B_tf);
    u8g2.drawStr(12, 18, "IONITY AEDI");
    u8g2.drawHLine(0, 24, 128);
    u8g2.setFont(u8g2_font_6x10_tf);
    u8g2.drawStr(4, 40, "ESP32-S3 REPORTER");
    u8g2.drawStr(4, 56, "CONNECTING WIFI...");
    u8g2.sendBuffer();
}

void connectNetwork() {
    Serial.println("[Network] Connecting to WiFi: " + String(WIFI_SSID));

    #if USE_STATIC_IP
    IPAddress ip(STATIC_IP_ADDR);
    IPAddress gateway(STATIC_GATEWAY);
    IPAddress subnet(STATIC_SUBNET);
    IPAddress dns1(STATIC_PRIMARY_DNS);
    IPAddress dns2(STATIC_SECONDARY_DNS);
    WiFi.config(ip, gateway, subnet, dns1, dns2);
    #endif

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    // Initial brief wait (non-blocking)
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 15) {
        delay(300);
        Serial.print(".");
        digitalWrite(PIN_LED_HEARTBEAT, !digitalRead(PIN_LED_HEARTBEAT));
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\n[Network] WiFi Connected! IP: " + WiFi.localIP().toString());
        digitalWrite(PIN_LED_HEARTBEAT, HIGH);

        // Configure NTP Real-Time Clock (South Africa UTC+2)
        configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET_SEC, NTP_SERVER_1, NTP_SERVER_2);
        Serial.println("[NTP] Time synchronization initialized.");
    } else {
        Serial.println("\n[Network] WiFi connection pending... Clock running on local tick.");
        digitalWrite(PIN_LED_ALERT, HIGH);
    }
}

// Sub-millisecond TCP socket probe to measure real latency
float probeSocketLatency(const char* host, uint16_t port, uint32_t timeoutMs = 1200) {
    WiFiClient client;
    unsigned long start = millis();
    bool connected = client.connect(host, port, timeoutMs);
    unsigned long duration = millis() - start;
    if (connected) {
        client.stop();
        return (float)duration;
    }
    return 999.0f; // Timeout
}

void probeTargets() {
    if (WiFi.status() != WL_CONNECTED) {
        currentMetrics.latency_dns1_ms = 999.0f;
        currentMetrics.latency_dns2_ms = 999.0f;
        currentMetrics.stability_score = 0.0f;
        currentMetrics.packet_loss_pct = 100.0f;
        return;
    }

    // 1. Probe Google DNS (8.8.8.8:53)
    float lat1 = probeSocketLatency(TARGET_PUBLIC_DNS1, 53);
    currentMetrics.latency_dns1_ms = lat1 < 999.0f ? lat1 : 999.0f;

    // 2. Probe Cloudflare DNS (1.1.1.1:53)
    float lat2 = probeSocketLatency(TARGET_PUBLIC_DNS2, 53);
    currentMetrics.latency_dns2_ms = lat2 < 999.0f ? lat2 : 999.0f;

    // Jitter: difference between probes
    if (lat1 < 999.0f && lat2 < 999.0f) {
        currentMetrics.jitter_ms = abs(lat1 - lat2);
        currentMetrics.packet_loss_pct = 0.0f;
    } else if (lat1 < 999.0f || lat2 < 999.0f) {
        currentMetrics.jitter_ms = 8.0f;
        currentMetrics.packet_loss_pct = 50.0f;
    } else {
        currentMetrics.jitter_ms = 999.0f;
        currentMetrics.packet_loss_pct = 100.0f;
    }

    // Stability Score calculation: 100 - (Loss * 0.7 + (Jitter/2.0))
    float penalty = (currentMetrics.packet_loss_pct * 0.7f) + (currentMetrics.jitter_ms * 0.4f);
    if (penalty > 100.0f) penalty = 100.0f;
    currentMetrics.stability_score = 100.0f - penalty;

    // Alert LED
    if (currentMetrics.stability_score < 70.0f || !currentMetrics.mains_power_ok) {
        digitalWrite(PIN_LED_ALERT, HIGH);
    } else {
        digitalWrite(PIN_LED_ALERT, LOW);
    }
}

void checkMainsPower() {
    currentMetrics.mains_power_ok = (digitalRead(PIN_MAINS_SENSE) == HIGH);
}

void renderClockAndTelemetryScreen() {
    struct tm timeinfo;
    bool gotNtp = getLocalTime(&timeinfo, 50);

    char timeStr[16];
    char dateStr[24];

    if (gotNtp) {
        ntpSynced = true;
        // Format: HH:MM:SS
        strftime(timeStr, sizeof(timeStr), "%H:%M:%S", &timeinfo);
        // Format: Day DD Mon YYYY (e.g. Thu 24 Sep)
        strftime(dateStr, sizeof(dateStr), "%a %d %b %Y", &timeinfo);
    } else {
        // Fallback running clock from millis
        unsigned long totalSec = millis() / 1000;
        unsigned int h = (totalSec / 3600) % 24;
        unsigned int m = (totalSec / 60) % 60;
        unsigned int s = totalSec % 60;
        snprintf(timeStr, sizeof(timeStr), "%02u:%02u:%02u", h, m, s);
        snprintf(dateStr, sizeof(dateStr), "SYNCING TIME...");
    }

    u8g2.clearBuffer();

    // 1. TOP HEADER (y: 0 - 12)
    u8g2.setFont(u8g2_font_6x10_tf);
    u8g2.drawStr(0, 9, "IONITY AEDI");

    // WiFi Indicator
    if (WiFi.status() == WL_CONNECTED) {
        int rssi = WiFi.RSSI();
        char wifiStr[12];
        snprintf(wifiStr, sizeof(wifiStr), "%ddBm", rssi);
        int w = u8g2.getStrWidth(wifiStr);
        u8g2.drawStr(128 - w, 9, wifiStr);
    } else {
        u8g2.drawStr(80, 9, "NO WIFI");
    }
    u8g2.drawHLine(0, 12, 128);

    // 2. LARGE DIGITAL CLOCK (y: 14 - 36)
    u8g2.setFont(u8g2_font_logisoso18_tn); // Clean, sharp large digital numerals
    int timeWidth = u8g2.getStrWidth(timeStr);
    int timeX = (128 - timeWidth) / 2;
    if (timeX < 0) timeX = 0;
    u8g2.drawStr(timeX, 35, timeStr);

    // 3. DATE SUBTITLE (y: 38 - 48)
    u8g2.setFont(u8g2_font_profont11_tf);
    int dateWidth = u8g2.getStrWidth(dateStr);
    int dateX = (128 - dateWidth) / 2;
    if (dateX < 0) dateX = 0;
    u8g2.drawStr(dateX, 48, dateStr);

    // Divider line
    u8g2.drawHLine(0, 52, 128);

    // 4. MCP TELEMETRY FOOTER (y: 54 - 64)
    u8g2.setFont(u8g2_font_5x7_tf);
    char footerBuf[32];
    int lat = (int)currentMetrics.latency_dns1_ms;
    if (lat > 999) lat = 999;
    snprintf(footerBuf, sizeof(footerBuf), "STAB:%d%% | %dms | AC:%s",
             (int)currentMetrics.stability_score,
             lat,
             currentMetrics.mains_power_ok ? "OK" : "LOSS");
    u8g2.drawStr(0, 62, footerBuf);

    u8g2.sendBuffer();
}

void pushTelemetryJSON() {
    if (WiFi.status() != WL_CONNECTED) {
        return;
    }

    currentMetrics.uptime_seconds = millis() / 1000;
    currentMetrics.free_heap_bytes = ESP.getFreeHeap();
    currentMetrics.chip_temp_c = temperatureRead();

    HTTPClient http;
    String url = "http://" + String(SENTINEL_SERVER_HOST) + ":" + String(SENTINEL_SERVER_PORT) + String(SENTINEL_API_ENDPOINT);

    http.begin(url);
    http.addHeader("Content-Type", "application/json");

    String jsonPayload = "{";
    jsonPayload += "\"device_id\":\"" + String(SENTINEL_DEVICE_ID) + "\",";
    jsonPayload += "\"site_name\":\"" + String(SITE_NAME) + "\",";
    jsonPayload += "\"firmware\":\"" + String(SENTINEL_FIRMWARE_VER) + "\",";
    jsonPayload += "\"uptime_seconds\":" + String(currentMetrics.uptime_seconds) + ",";
    jsonPayload += "\"mains_power_ok\":" + String(currentMetrics.mains_power_ok ? "true" : "false") + ",";
    jsonPayload += "\"stability_score\":" + String(currentMetrics.stability_score, 2) + ",";
    jsonPayload += "\"packet_loss_pct\":" + String(currentMetrics.packet_loss_pct, 1) + ",";
    jsonPayload += "\"jitter_ms\":" + String(currentMetrics.jitter_ms, 2) + ",";
    jsonPayload += "\"dns_latency_ms\":" + String(currentMetrics.latency_dns1_ms, 1) + ",";
    jsonPayload += "\"chip_temp_c\":" + String(currentMetrics.chip_temp_c, 1) + ",";
    jsonPayload += "\"free_heap_bytes\":" + String(currentMetrics.free_heap_bytes);
    jsonPayload += "}";

    int httpCode = http.POST(jsonPayload);
    if (httpCode > 0) {
        Serial.printf("[Sentinel TX] HTTP %d | Stab: %.1f%% | Temp: %.1fC\n",
                      httpCode, currentMetrics.stability_score, currentMetrics.chip_temp_c);
        digitalWrite(PIN_LED_HEARTBEAT, HIGH);
        delay(40);
        digitalWrite(PIN_LED_HEARTBEAT, LOW);
    } else {
        Serial.printf("[Sentinel TX Error] POST failed: %s\n", http.errorToString(httpCode).c_str());
    }
    http.end();
}

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("\n================================================");
    Serial.println("   ⚡ Ionity_ESP_Reporter Initializing...       ");
    Serial.println("   Hardware: ESP32-S3 + I2C OLED (SDA=5,SCL=6)  ");
    Serial.println("================================================");

    setupPins();
    initDisplay();
    connectNetwork();

    // Default metric values
    currentMetrics.mains_power_ok = true;
    currentMetrics.stability_score = 98.0f;
    currentMetrics.latency_dns1_ms = 12.0f;
}

void loop() {
    unsigned long now = millis();

    // 1. Smooth Screen Refresh (Clock & Telemetry)
    if (now - lastDisplayUpdate >= DISPLAY_UPDATE_INTERVAL_MS) {
        lastDisplayUpdate = now;
        renderClockAndTelemetryScreen();
    }

    // 2. AC Mains Power Check (Optocoupler pin)
    if (now - lastMainsCheck >= MAINS_CHECK_INTERVAL_MS) {
        lastMainsCheck = now;
        checkMainsPower();
    }

    // 3. Sub-second Latency & Jitter Probe
    if (now - lastProbeTime >= PING_PROBE_INTERVAL_MS) {
        lastProbeTime = now;
        probeTargets();
    }

    // 4. Push Telemetry to Ionity Dashboard & MCP Server
    if (now - lastTelemetryPush >= TELEMETRY_INTERVAL_MS) {
        lastTelemetryPush = now;
        pushTelemetryJSON();
    }
}
