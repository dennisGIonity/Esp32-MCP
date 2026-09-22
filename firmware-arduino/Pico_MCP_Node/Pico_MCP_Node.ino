// ===========================================================================
// AEDI - IONITY GLOBAL | Pico / Pico W Fleet Node (Arduino IDE, arduino-pico)
// Doc ID: DOC-2026-09-ESP32MCP-PICO | Version 1.0.0 | Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved
// ---------------------------------------------------------------------------
// Same telemetry contract as the ESP32 node, so the server, dashboard and MCP
// tools treat it identically.
//
// TWO PATHS, ALWAYS BOTH:
//   * WiFi + HTTP POST   when built for a W board and the radio associates
//   * "TLM {json}" lines on USB serial, every cycle, unconditionally
// The serial path is what lets a plain (non-W) Pico report at all:
// scripts/serial_bridge.py on the host forwards those lines to the server.
// On a W board it is also a free debug tap.
//
// Board: Tools > Board > Raspberry Pi Pico/RP2040/RP2350 > (Pico W | Pico 2 W | Pico | Pico 2)
// ===========================================================================

#include <Arduino.h>
#include <ArduinoJson.h>
#include <pico/unique_id.h>

#if defined(ARDUINO_RASPBERRY_PI_PICO_W) || defined(ARDUINO_RASPBERRY_PI_PICO_2W)
  #define HAS_WIFI 1
  #include <WiFi.h>
  #include <HTTPClient.h>
  #include "secrets.h"
#else
  #define HAS_WIFI 0
#endif

#define FW_VERSION          "1.0.0"
#define FW_PRODUCT          "ionity-pico-mcp-node"
#define DEFAULT_SITE        "lab"
#define DEFAULT_GROUP       "bench"
#define SERVER_HOST         "192.168.0.3"      // fallback; see note below
#define SERVER_PORT         8099
#define INGEST_PATH         "/api/v1/telemetry"
#define TELEMETRY_MS        10000
#define WIFI_RETRY_MS       15000
// Not "PIN_LED": the arduino-pico core already defines LED_BUILTIN as PIN_LED,
// so reusing that name creates a circular macro.
#define NODE_LED            LED_BUILTIN

// Note: the Pico W's mDNS stack has no simple host lookup equivalent to the
// ESP32's MDNS.queryHost, so this node uses the fallback address for its
// WiFi path. The serial path needs no address at all -- the bridge on the
// host already knows where the server is.

String   gDeviceId;
uint32_t gSeq = 0, gTxOk = 0, gTxFail = 0;
unsigned long lastTx = 0, lastWifiTry = 0;
bool     gWifiUp = false;

String boardId() {
  char buf[2 * PICO_UNIQUE_BOARD_ID_SIZE_BYTES + 1];
  pico_get_unique_board_id_string(buf, sizeof(buf));
  String s(buf);
  s.toLowerCase();
  return s;
}

#if HAS_WIFI
void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) { gWifiUp = true; return; }
  gWifiUp = false;
  if (millis() - lastWifiTry < WIFI_RETRY_MS && lastWifiTry != 0) return;
  lastWifiTry = millis();
  Serial.println("[IONITY-PICO] WiFi connecting to \"" WIFI_SSID "\"");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 12000) delay(250);
  gWifiUp = (WiFi.status() == WL_CONNECTED);
  if (gWifiUp) Serial.println("[IONITY-PICO] WiFi OK ip=" + WiFi.localIP().toString());
  else         Serial.println("[IONITY-PICO] WiFi not available - serial path only");
}

bool postHttp(const String &json) {
  if (!gWifiUp) return false;
  WiFiClient client;
  HTTPClient http;
  String url = String("http://") + SERVER_HOST + ":" + SERVER_PORT + INGEST_PATH;
  if (!http.begin(client, url)) return false;
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Fleet-Token", FLEET_TOKEN);
  int code = http.POST(json);
  http.end();
  return code >= 200 && code < 300;
}
#endif

String buildJson() {
  JsonDocument doc;
  doc["device_id"] = gDeviceId;
  doc["site"]      = DEFAULT_SITE;
  doc["group"]     = DEFAULT_GROUP;
  doc["label"]     = gDeviceId;
  doc["fw"]        = FW_VERSION;
  doc["product"]   = FW_PRODUCT;
  doc["uptime_s"]  = millis() / 1000;
  doc["seq"]       = gSeq++;

  JsonObject m = doc["metrics"].to<JsonObject>();
  m["temp_c"]          = round(analogReadTemp() * 10) / 10.0;   // on-die sensor
  m["free_heap_bytes"] = rp2040.getFreeHeap();
  m["cpu_mhz"]         = rp2040.f_cpu() / 1000000;
#if HAS_WIFI
  if (gWifiUp) m["rssi_dbm"] = WiFi.RSSI();
#endif

  JsonObject n = doc["net"].to<JsonObject>();
#if HAS_WIFI
  n["ip"]        = gWifiUp ? WiFi.localIP().toString() : String("");
  n["transport"] = gWifiUp ? "http" : "serial";
#else
  n["transport"] = "serial";
#endif

  String out;
  serializeJson(doc, out);
  return out;
}

void setup() {
  Serial.begin(115200);
  pinMode(NODE_LED, OUTPUT);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) delay(10);   // let USB CDC come up

  gDeviceId = "pico-" + boardId();
  Serial.println("[IONITY-PICO] =============================================");
  Serial.println("[IONITY-PICO] IONITY PICO FLEET NODE | fw " FW_VERSION);
  Serial.println("[IONITY-PICO] (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd");
  Serial.println("[IONITY-PICO] device_id = " + gDeviceId);
#if HAS_WIFI
  Serial.println("[IONITY-PICO] build: WiFi-capable (W board)");
  ensureWifi();
#else
  Serial.println("[IONITY-PICO] build: no radio - reporting over USB serial");
#endif
}

void loop() {
#if HAS_WIFI
  ensureWifi();
#endif
  if (millis() - lastTx >= TELEMETRY_MS || lastTx == 0) {
    lastTx = millis();
    String json = buildJson();

    // Always emit on serial: this is the path a non-W Pico depends on.
    Serial.print("TLM ");
    Serial.println(json);

    bool ok = false;
#if HAS_WIFI
    ok = postHttp(json);
#endif
    if (ok) gTxOk++; else gTxFail++;

    digitalWrite(NODE_LED, HIGH); delay(30); digitalWrite(NODE_LED, LOW);
  }
  delay(20);
}
