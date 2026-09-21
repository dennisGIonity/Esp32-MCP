// ===========================================================================
// AEDI - IONITY GLOBAL | ESP32-MCP Fleet Node Firmware
// Doc ID: DOC-2026-09-ESP32MCP-FW | Version 1.0.0 | Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved
// ---------------------------------------------------------------------------
// ONE BINARY, MANY DEVICES.
//   * device_id derived from eFuse MAC  -> no per-unit code edits
//   * site/group/label read from NVS    -> provision without reflashing
//   * MQTT primary, HTTP POST fallback  -> survives a broker outage
//   * retained status + LWT             -> server sees offline in ~90s
//   * offline ring buffer               -> no data loss across short dropouts
// ===========================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <esp_system.h>

#include "config.h"

// ---------------------------------------------------------------------------
// Runtime identity (resolved once at boot)
// ---------------------------------------------------------------------------
String gDeviceId;
String gSite;
String gGroup;
String gLabel;

String tTelemetry, tStatus, tEvent, tCmd, tCmdResult;

Preferences prefs;
WiFiClient netClient;
PubSubClient mqtt(netClient);

// ---------------------------------------------------------------------------
// Sample + fleet state
// ---------------------------------------------------------------------------
struct Sample {
  uint32_t ts_ms;
  float    temp_c;
  float    analog_v;
  bool     digital_state;
  float    rssi_dbm;
  float    latency_ms;
  float    packet_loss_pct;
  uint32_t free_heap;
};

Sample  gLatest;
Sample  gBuffer[OFFLINE_BUFFER_SLOTS];
uint8_t gBufCount = 0;

uint8_t  gMqttFails   = 0;
bool     gUseHttp     = false;
uint32_t gBootEpoch   = 0;
uint32_t gTxOk        = 0;
uint32_t gTxFail      = 0;

unsigned long lastSample = 0, lastTelemetry = 0, lastStatus = 0;
unsigned long lastWifiTry = 0, lastMqttTry = 0, lastProbe = 0;

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------
void logln(const String &m) { Serial.println(String(LOG_PREFIX) + m); }

// ---------------------------------------------------------------------------
// Identity: MAC-derived id, NVS-backed metadata
// ---------------------------------------------------------------------------
String macSuffix() {
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  char buf[13];
  snprintf(buf, sizeof(buf), "%02x%02x%02x%02x%02x%02x",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(buf);
}

void loadIdentity() {
  prefs.begin("ionity", false);
  gDeviceId = prefs.getString("device_id", "");
  if (gDeviceId.length() == 0) {
    gDeviceId = String(DEVICE_ID_PREFIX) + "-" + macSuffix();
    prefs.putString("device_id", gDeviceId);
  }
  gSite  = prefs.getString("site",  DEFAULT_SITE);
  gGroup = prefs.getString("group", DEFAULT_GROUP);
  gLabel = prefs.getString("label", gDeviceId);
  prefs.end();

  String base = String(MQTT_ROOT) + "/" + gSite + "/" + gDeviceId + "/";
  tTelemetry = base + MQTT_CH_TELEMETRY;
  tStatus    = base + MQTT_CH_STATUS;
  tEvent     = base + MQTT_CH_EVENT;
  tCmd       = base + MQTT_CH_CMD;
  tCmdResult = base + MQTT_CH_CMD_RESULT;

  logln("device_id = " + gDeviceId);
  logln("site/group = " + gSite + "/" + gGroup);
  logln("topic base = " + base);
}

void persistIdentity(const char *key, const String &val) {
  prefs.begin("ionity", false);
  prefs.putString(key, val);
  prefs.end();
}

// ---------------------------------------------------------------------------
// Sensors
// Replace the body of sampleSensors() with your real transducers. The JSON
// schema is open: anything you add under "metrics" is stored and queryable
// by the MCP layer without a server change.
// ---------------------------------------------------------------------------
void sampleSensors() {
  gLatest.ts_ms         = millis();
  gLatest.digital_state = (digitalRead(PIN_DIGITAL_SENSE) == HIGH);
  gLatest.analog_v      = (analogRead(PIN_ANALOG_SENSE) / 4095.0f) * 3.3f;
  gLatest.rssi_dbm      = (WiFi.status() == WL_CONNECTED) ? WiFi.RSSI() : -127;
  gLatest.free_heap     = ESP.getFreeHeap();
#if defined(BOARD_ESP32S3) || defined(BOARD_ESP32C3)
  gLatest.temp_c        = temperatureRead();
#else
  gLatest.temp_c        = NAN;
#endif

  digitalWrite(PIN_LED_ALERT, gLatest.digital_state ? LOW : HIGH);
}

// ---------------------------------------------------------------------------
// Optional network probe (lifted from RouterProject sentinel, trimmed)
// ---------------------------------------------------------------------------
#if PROBE_ENABLED
#include <ESP32Ping.h>
void runProbe() {
  if (WiFi.status() != WL_CONNECTED) {
    gLatest.latency_ms = 999.0f;
    gLatest.packet_loss_pct = 100.0f;
    return;
  }
  int ok = 0; float total = 0;
  if (Ping.ping(PROBE_TARGET_1, 2)) { ok++; total += Ping.averageTime(); }
  if (Ping.ping(PROBE_TARGET_2, 2)) { ok++; total += Ping.averageTime(); }
#if PROBE_GATEWAY_AUTO
  IPAddress gw = WiFi.gatewayIP();
  int targets = 3;
  if (Ping.ping(gw, 2)) { ok++; total += Ping.averageTime(); }
#else
  int targets = 2;
#endif
  gLatest.latency_ms      = ok ? (total / ok) : 999.0f;
  gLatest.packet_loss_pct = (1.0f - ((float)ok / targets)) * 100.0f;
}
#else
void runProbe() { gLatest.latency_ms = NAN; gLatest.packet_loss_pct = NAN; }
#endif

// ---------------------------------------------------------------------------
// Payload builder - single source of truth for both MQTT and HTTP
// ---------------------------------------------------------------------------
String buildTelemetryJson(const Sample &s) {
  JsonDocument doc;
  doc["device_id"] = gDeviceId;
  doc["site"]      = gSite;
  doc["group"]     = gGroup;
  doc["label"]     = gLabel;
  doc["fw"]        = FW_VERSION;
  doc["product"]   = FW_PRODUCT;
  doc["uptime_s"]  = millis() / 1000;
  doc["seq"]       = gTxOk + gTxFail;

  JsonObject m = doc["metrics"].to<JsonObject>();
  if (!isnan(s.temp_c))          m["temp_c"]          = round(s.temp_c * 10) / 10.0;
  m["analog_v"]        = round(s.analog_v * 1000) / 1000.0;
  m["digital_state"]   = s.digital_state ? 1 : 0;
  m["rssi_dbm"]        = s.rssi_dbm;
  m["free_heap_bytes"] = s.free_heap;
  if (!isnan(s.latency_ms))      m["latency_ms"]      = round(s.latency_ms * 10) / 10.0;
  if (!isnan(s.packet_loss_pct)) m["packet_loss_pct"] = s.packet_loss_pct;

  JsonObject n = doc["net"].to<JsonObject>();
  n["ip"]        = WiFi.localIP().toString();
  n["transport"] = gUseHttp ? "http" : "mqtt";

  String out;
  serializeJson(doc, out);
  return out;
}

String buildStatusJson(const char *state) {
  JsonDocument doc;
  doc["device_id"] = gDeviceId;
  doc["site"]      = gSite;
  doc["group"]     = gGroup;
  doc["label"]     = gLabel;
  doc["fw"]        = FW_VERSION;
  doc["state"]     = state;
  doc["ip"]        = WiFi.localIP().toString();
  doc["uptime_s"]  = millis() / 1000;
  doc["tx_ok"]     = gTxOk;
  doc["tx_fail"]   = gTxFail;
  doc["transport"] = gUseHttp ? "http" : "mqtt";
  String out;
  serializeJson(doc, out);
  return out;
}

// ---------------------------------------------------------------------------
// Inbound commands (server -> device) over MQTT
// ---------------------------------------------------------------------------
void publishCmdResult(const String &cmdId, bool ok, const String &detail) {
  JsonDocument doc;
  doc["device_id"] = gDeviceId;
  doc["cmd_id"]    = cmdId;
  doc["ok"]        = ok;
  doc["detail"]    = detail;
  String out; serializeJson(doc, out);
  mqtt.publish(tCmdResult.c_str(), out.c_str(), false);
}

void onMqttMessage(char *topic, byte *payload, unsigned int len) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) return;

  String action = doc["action"] | "";
  String cmdId  = doc["cmd_id"] | "";
  logln("cmd <- " + action);

  if (action == "reboot") {
    publishCmdResult(cmdId, true, "rebooting");
    delay(250);
    ESP.restart();
  } else if (action == "identify") {
    for (int i = 0; i < 10; i++) {
      digitalWrite(PIN_LED_HEARTBEAT, !digitalRead(PIN_LED_HEARTBEAT));
      delay(120);
    }
    publishCmdResult(cmdId, true, "blinked");
  } else if (action == "set_meta") {
    if (doc["site"].is<const char*>())  { gSite  = doc["site"].as<String>();  persistIdentity("site", gSite); }
    if (doc["group"].is<const char*>()) { gGroup = doc["group"].as<String>(); persistIdentity("group", gGroup); }
    if (doc["label"].is<const char*>()) { gLabel = doc["label"].as<String>(); persistIdentity("label", gLabel); }
    publishCmdResult(cmdId, true, "metadata stored; rebooting to re-topic");
    delay(250);
    ESP.restart();
  } else if (action == "ping") {
    publishCmdResult(cmdId, true, "pong");
  } else {
    publishCmdResult(cmdId, false, "unknown action");
  }
}

// ---------------------------------------------------------------------------
// Connectivity
// ---------------------------------------------------------------------------
void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - lastWifiTry < WIFI_RETRY_MS) return;
  lastWifiTry = millis();

  logln("WiFi connecting to " WIFI_SSID " ...");
  WiFi.mode(WIFI_STA);
  WiFi.setHostname((String(OTA_HOSTNAME_PREFIX) + macSuffix()).c_str());
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

bool ensureMqtt() {
  if (mqtt.connected()) { gMqttFails = 0; gUseHttp = false; return true; }
  if (WiFi.status() != WL_CONNECTED) return false;
  if (millis() - lastMqttTry < MQTT_RETRY_MS) return false;
  lastMqttTry = millis();

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setKeepAlive(MQTT_KEEPALIVE_S);
  mqtt.setBufferSize(1024);
  mqtt.setCallback(onMqttMessage);

  String willPayload = buildStatusJson("offline");
  bool ok = (strlen(MQTT_USERNAME) > 0)
    ? mqtt.connect(gDeviceId.c_str(), MQTT_USERNAME, MQTT_PASSWORD,
                   tStatus.c_str(), 1, true, willPayload.c_str())
    : mqtt.connect(gDeviceId.c_str(), nullptr, nullptr,
                   tStatus.c_str(), 1, true, willPayload.c_str());

  if (ok) {
    logln("MQTT connected");
    gMqttFails = 0; gUseHttp = false;
    mqtt.subscribe(tCmd.c_str(), 1);
    mqtt.subscribe((String(MQTT_ROOT) + "/broadcast/cmd").c_str(), 1);
    mqtt.publish(tStatus.c_str(), buildStatusJson("online").c_str(), true);
    return true;
  }

  gMqttFails++;
  logln("MQTT connect failed (" + String(gMqttFails) + "/" + String(MQTT_FAIL_THRESHOLD) + ")");
#if HTTP_FALLBACK_ENABLED
  if (gMqttFails >= MQTT_FAIL_THRESHOLD && !gUseHttp) {
    gUseHttp = true;
    logln("--> falling back to HTTP ingest");
  }
#endif
  return false;
}

// ---------------------------------------------------------------------------
// Transmit paths
// ---------------------------------------------------------------------------
bool sendHttp(const String &json) {
  if (WiFi.status() != WL_CONNECTED) return false;
  HTTPClient http;
  String url = "http://" + String(SERVER_HOST) + ":" + String(SERVER_HTTP_PORT) + HTTP_INGEST_PATH;
  http.setConnectTimeout(4000);
  http.setTimeout(6000);
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Fleet-Token", FLEET_TOKEN);
  int code = http.POST(json);
  http.end();
  return (code >= 200 && code < 300);
}

bool transmit(const String &json) {
  bool ok = false;
  if (!gUseHttp && mqtt.connected()) {
    ok = mqtt.publish(tTelemetry.c_str(), json.c_str(), false);
  }
#if HTTP_FALLBACK_ENABLED
  if (!ok) ok = sendHttp(json);
#endif
  if (ok) { gTxOk++; } else { gTxFail++; }
  return ok;
}

void bufferSample(const Sample &s) {
  if (gBufCount < OFFLINE_BUFFER_SLOTS) {
    gBuffer[gBufCount++] = s;
  } else {                                  // drop oldest
    memmove(&gBuffer[0], &gBuffer[1], sizeof(Sample) * (OFFLINE_BUFFER_SLOTS - 1));
    gBuffer[OFFLINE_BUFFER_SLOTS - 1] = s;
  }
}

void flushBuffer() {
  while (gBufCount > 0) {
    if (!transmit(buildTelemetryJson(gBuffer[0]))) return;   // still down
    memmove(&gBuffer[0], &gBuffer[1], sizeof(Sample) * (gBufCount - 1));
    gBufCount--;
    delay(20);                                               // be kind to broker
  }
}

void pushTelemetry() {
  String json = buildTelemetryJson(gLatest);
  if (transmit(json)) {
    digitalWrite(PIN_LED_HEARTBEAT, HIGH); delay(25);
    digitalWrite(PIN_LED_HEARTBEAT, LOW);
    flushBuffer();
  } else {
    bufferSample(gLatest);
    logln("tx failed - buffered (" + String(gBufCount) + " queued)");
  }
}

// ---------------------------------------------------------------------------
// OTA
// ---------------------------------------------------------------------------
#if OTA_ENABLED
void setupOta() {
  ArduinoOTA.setHostname((String(OTA_HOSTNAME_PREFIX) + macSuffix()).c_str());
  ArduinoOTA.setPassword(OTA_PASSWORD);
  ArduinoOTA.onStart([]() { logln("OTA start"); });
  ArduinoOTA.onEnd([]()   { logln("OTA done"); });
  ArduinoOTA.onError([](ota_error_t e) { logln("OTA error " + String(e)); });
  ArduinoOTA.begin();
}
#endif

// ---------------------------------------------------------------------------
// Setup / loop
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(600);
  Serial.println();
  logln("=========================================================");
  logln(" IONITY ESP32-MCP FLEET NODE  |  fw " FW_VERSION);
  logln(" (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd");
  logln("=========================================================");

  pinMode(PIN_LED_HEARTBEAT, OUTPUT);
  pinMode(PIN_LED_ALERT, OUTPUT);
  pinMode(PIN_DIGITAL_SENSE, INPUT_PULLUP);
  digitalWrite(PIN_LED_HEARTBEAT, LOW);
  digitalWrite(PIN_LED_ALERT, LOW);

  loadIdentity();
  ensureWifi();

  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
    delay(250); Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) logln("WiFi OK  ip=" + WiFi.localIP().toString());
  else logln("WiFi FAILED - continuing in buffer mode");

#if OTA_ENABLED
  setupOta();
#endif
  ensureMqtt();
  sampleSensors();
}

void loop() {
  unsigned long now = millis();

  ensureWifi();
  ensureMqtt();
  if (mqtt.connected()) mqtt.loop();
#if OTA_ENABLED
  ArduinoOTA.handle();
#endif

  if (now - lastSample >= SENSOR_SAMPLE_MS) { lastSample = now; sampleSensors(); }
  if (now - lastProbe  >= PROBE_INTERVAL_MS) { lastProbe  = now; runProbe(); }

  if (now - lastTelemetry >= TELEMETRY_INTERVAL_MS) {
    lastTelemetry = now;
    pushTelemetry();
  }

  if (now - lastStatus >= HEARTBEAT_STATUS_MS) {
    lastStatus = now;
    if (mqtt.connected())
      mqtt.publish(tStatus.c_str(), buildStatusJson("online").c_str(), true);
  }

  delay(10);
}
