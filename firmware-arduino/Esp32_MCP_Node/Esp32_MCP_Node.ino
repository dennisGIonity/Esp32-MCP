// ===========================================================================
// AEDI - IONITY GLOBAL | ESP32-MCP Fleet Node  (Arduino IDE sketch)
// Doc ID: DOC-2026-09-ESP32MCP-FW | Version 1.0.0 | Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved
// ---------------------------------------------------------------------------
// ONE SKETCH, MANY DEVICES.
//   * device_id from eFuse MAC        -> no per-unit edits
//   * site/group/label from NVS       -> provision without reflashing
//   * MQTT primary, HTTP POST fallback-> survives a broker outage
//   * retained status + Last Will     -> server sees offline in ~90s
//   * offline ring buffer             -> no data loss across short dropouts
//
// Board:    Tools > Board > esp32 > (ESP32S3 Dev Module | ESP32 Dev Module | ...)
// Requires: PubSubClient, ArduinoJson (Library Manager)
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
#if __has_include(<esp_mac.h>)
  #include <esp_mac.h>
#endif

#include "config.h"

// ---------------------------------------------------------------------------
// Runtime identity (resolved once at boot)
// ---------------------------------------------------------------------------
String gDeviceId, gSite, gGroup, gLabel;
String tTelemetry, tStatus, tEvent, tCmd, tCmdResult;

Preferences prefs;
WiFiClient  netClient;
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

uint8_t  gMqttFails = 0;
bool     gUseHttp   = false;
uint32_t gTxOk = 0, gTxFail = 0;

unsigned long lastSample = 0, lastTelemetry = 0, lastStatus = 0;
unsigned long lastWifiTry = 0, lastMqttTry = 0, lastProbe = 0;

void logln(const String &m) { Serial.println(String(LOG_PREFIX) + m); }

// ---------------------------------------------------------------------------
// Identity
// ---------------------------------------------------------------------------
String macSuffix() {
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  char buf[13];
  snprintf(buf, sizeof(buf), "%02x%02x%02x%02x%02x%02x",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(buf);
}

void persistIdentity(const char *key, const String &val) {
  prefs.begin("ionity", false);
  prefs.putString(key, val);
  prefs.end();
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
  tCmd       = base + MQTT_CH_CMD;
  tCmdResult = base + MQTT_CH_CMD_RESULT;

  logln("device_id  = " + gDeviceId);
  logln("site/group = " + gSite + "/" + gGroup);
  logln("topic base = " + base);
  logln("server     = http://" + String(SERVER_HOST) + ":" + String(SERVER_HTTP_PORT) + HTTP_INGEST_PATH);
}

// ---------------------------------------------------------------------------
// Sensors
// Swap in your real transducers here. Anything you add under "metrics" in the
// payload is stored, charted and MCP-queryable with NO server change.
// ---------------------------------------------------------------------------
void sampleSensors() {
  gLatest.ts_ms         = millis();
  gLatest.digital_state = (digitalRead(PIN_DIGITAL_SENSE) == HIGH);
  gLatest.analog_v      = (analogRead(PIN_ANALOG_SENSE) / 4095.0f) * 3.3f;
  // Report RSSI only when actually associated. Emitting a sentinel like -127
  // while disconnected reads as a real reading downstream and trips the
  // low_rssi alert -- better to omit the metric entirely for that sample.
  gLatest.rssi_dbm      = (WiFi.status() == WL_CONNECTED) ? WiFi.RSSI() : NAN;
  gLatest.free_heap     = ESP.getFreeHeap();

#if defined(CONFIG_IDF_TARGET_ESP32)
  gLatest.temp_c = NAN;              // classic ESP32 has no usable internal sensor
#else
  gLatest.temp_c = temperatureRead();
#endif

  digitalWrite(PIN_LED_ALERT, gLatest.digital_state ? LOW : HIGH);
}

void runProbe() {
#if PROBE_ENABLED
  // See config.h -- enable via the PlatformIO build, which pins ESP32Ping.
#endif
  gLatest.latency_ms = NAN;
  gLatest.packet_loss_pct = NAN;
}

// ---------------------------------------------------------------------------
// Payload builders - one source of truth for MQTT and HTTP alike
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
  if (!isnan(s.rssi_dbm))        m["rssi_dbm"]        = s.rssi_dbm;
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

  logln("WiFi connecting to \"" WIFI_SSID "\" ...");
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
    : mqtt.connect(gDeviceId.c_str(), NULL, NULL,
                   tStatus.c_str(), 1, true, willPayload.c_str());

  if (ok) {
    logln("MQTT connected -> " MQTT_HOST);
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
    logln("--> no broker; falling back to HTTP ingest (this is expected if mosquitto isn't running)");
  }
#endif
  return false;
}

// ---------------------------------------------------------------------------
// Transmit
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
  if (code <= 0) logln("HTTP error: " + http.errorToString(code));
  else if (code >= 300) logln("HTTP " + String(code) + ": " + http.getString().substring(0, 120));
  http.end();
  return (code >= 200 && code < 300);
}

bool transmit(const String &json) {
  bool ok = false;
  if (!gUseHttp && mqtt.connected()) ok = mqtt.publish(tTelemetry.c_str(), json.c_str(), false);
#if HTTP_FALLBACK_ENABLED
  if (!ok) ok = sendHttp(json);
#endif
  if (ok) gTxOk++; else gTxFail++;
  return ok;
}

void bufferSample(const Sample &s) {
  if (gBufCount < OFFLINE_BUFFER_SLOTS) {
    gBuffer[gBufCount++] = s;
  } else {
    memmove(&gBuffer[0], &gBuffer[1], sizeof(Sample) * (OFFLINE_BUFFER_SLOTS - 1));
    gBuffer[OFFLINE_BUFFER_SLOTS - 1] = s;
  }
}

void flushBuffer() {
  while (gBufCount > 0) {
    if (!transmit(buildTelemetryJson(gBuffer[0]))) return;
    memmove(&gBuffer[0], &gBuffer[1], sizeof(Sample) * (gBufCount - 1));
    gBufCount--;
    delay(20);
  }
}

void pushTelemetry() {
  String json = buildTelemetryJson(gLatest);
  if (transmit(json)) {
    digitalWrite(PIN_LED_HEARTBEAT, HIGH); delay(25);
    digitalWrite(PIN_LED_HEARTBEAT, LOW);
    logln("TX ok via " + String(gUseHttp ? "HTTP" : "MQTT") +
          "  temp=" + String(gLatest.temp_c, 1) +
          "C rssi=" + String((int)gLatest.rssi_dbm) +
          "dBm heap=" + String(gLatest.free_heap) +
          "  (ok=" + String(gTxOk) + " fail=" + String(gTxFail) + ")");
    flushBuffer();
  } else {
    bufferSample(gLatest);
    logln("TX failed - buffered (" + String(gBufCount) + " queued)");
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
  logln("OTA ready as " + String(OTA_HOSTNAME_PREFIX) + macSuffix() + ".local");
}
#endif

// ---------------------------------------------------------------------------
// Setup / loop
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1200);                      // let native-USB CDC enumerate
  Serial.println();
  logln("=========================================================");
  logln(" IONITY ESP32-MCP FLEET NODE  |  fw " FW_VERSION);
  logln(" (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd");
  logln(" Policy 986 AED | Building Tomorrow, Today.");
  logln("=========================================================");

  pinMode(PIN_LED_HEARTBEAT, OUTPUT);
  pinMode(PIN_LED_ALERT, OUTPUT);
  pinMode(PIN_DIGITAL_SENSE, INPUT_PULLUP);
  digitalWrite(PIN_LED_HEARTBEAT, LOW);
  digitalWrite(PIN_LED_ALERT, LOW);

  // The probe metrics are only produced by the PlatformIO build. Mark them
  // NaN up front so a zero-initialised struct never reports a fabricated
  // "0 ms latency / 0% loss" that looks like a genuine measurement.
  gLatest.latency_ms      = NAN;
  gLatest.packet_loss_pct = NAN;
  gLatest.rssi_dbm        = NAN;

  loadIdentity();
  ensureWifi();

  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(250);
    Serial.print(".");
    digitalWrite(PIN_LED_HEARTBEAT, !digitalRead(PIN_LED_HEARTBEAT));
  }
  Serial.println();
  digitalWrite(PIN_LED_HEARTBEAT, LOW);

  if (WiFi.status() == WL_CONNECTED) {
    logln("WiFi OK   ip=" + WiFi.localIP().toString() +
          "  gw=" + WiFi.gatewayIP().toString() +
          "  rssi=" + String(WiFi.RSSI()) + "dBm");
#if OTA_ENABLED
    setupOta();
#endif
  } else {
    logln("WiFi FAILED - check SSID/password in secrets.h. Buffering locally.");
    digitalWrite(PIN_LED_ALERT, HIGH);
  }

  ensureMqtt();
  sampleSensors();
  logln("Boot complete. Reporting every " + String(TELEMETRY_INTERVAL_MS / 1000) + "s.");
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
