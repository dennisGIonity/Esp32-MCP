// ===========================================================================
// AEDI - IONITY GLOBAL | ESP32-MCP Fleet Node  (Arduino IDE sketch)
// Doc ID: DOC-2026-09-ESP32MCP-FW | Version 2.0.0 | Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved
// ---------------------------------------------------------------------------
// ONE IMAGE, ANY NETWORK, MANY DEVICES.
//   * device_id from eFuse MAC          -> no per-unit edits
//   * WiFi + MCP host + tokens in NVS   -> written over USB by the Ionity
//                                          Flasher; no recompile per network
//   * MQTT primary, HTTP POST fallback  -> survives a broker outage
//   * retained status + Last Will       -> server sees offline in ~90s
//   * offline ring buffer               -> no data loss across short dropouts
//   * on-device MCP server              -> tools/list + tools/call over HTTP
//                                          (:80/mcp) and over MQTT ("mcp" cmd)
//   * edge inference, actuators, state modes (STANDBY / ACTIVE /
//     INFERENCE_ACTIVE / LOW_POWER_SLEEP / FAILSAFE)
//
// Tabs: Provision (NVS + serial protocol), DeviceMcp (MCP server),
//       EdgeAI (inference), Actuators, Oled.
// Requires: PubSubClient, ArduinoJson 7, U8g2 (Library Manager)
// ===========================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <esp_system.h>
#include <esp_sleep.h>
#if __has_include(<esp_mac.h>)
  #include <esp_mac.h>
#endif

#include "config.h"
#include "NodeState.h"

// ---------------------------------------------------------------------------
// Runtime identity + configuration (resolved once at boot)
// ---------------------------------------------------------------------------
String gDeviceId, gSite, gGroup, gLabel;
String tTelemetry, tStatus, tEvent, tCmd, tCmdResult;
NodeConfig gCfg;

// Resolved server address + how we found it (reported in telemetry so a
// misrouted fleet is visible on the dashboard rather than silently dead).
String gServerHost = SERVER_HOST_FALLBACK;
String gServerVia  = "fallback";
uint8_t gConsecutiveFails = 0;

// Cleared by oledDetect() (Oled tab) when the display shares one of these pins.
bool gUseHeartbeatLed = true;
bool gUseAlertLed     = true;
bool gUseDigitalSense = true;
int8_t gOledSda = -1, gOledScl = -1;   // set by oledDetect() (Oled tab)

Preferences  prefs;
WiFiClient   netClient;
PubSubClient mqtt(netClient);
WebServer    mcpHttp(MCP_HTTP_PORT);
bool         gMcpHttpUp = false;

// ---------------------------------------------------------------------------
// Sample + fleet state
// ---------------------------------------------------------------------------
Sample  gLatest;
Sample  gBuffer[OFFLINE_BUFFER_SLOTS];
uint8_t gBufCount = 0;

uint8_t  gMqttFails = 0;
bool     gUseHttp   = false;
uint32_t gTxOk = 0, gTxFail = 0;

unsigned long lastSample = 0, lastTelemetry = 0, lastStatus = 0;
unsigned long lastWifiTry = 0, lastMqttTry = 0, lastProbe = 0, lastFast = 0;
bool gWifiKick = true;          // next ensureWifi() tries immediately (boot, new credentials)

// State mode + edge inference (EdgeAI / DeviceMcp tabs)
StateMode gMode = MODE_ACTIVE;
Ring      gRingAnalog, gRingRssi;
Inference gLastInference;
uint32_t  gInferenceRuns = 0;
String    gInfModel = "anomaly_zscore";   // model INFERENCE_ACTIVE runs

// Actuator state (Actuators tab). -1 = never driven.
float gAct[5] = {-1, -1, -1, -1, -1};     // led, alert_led, pwm0, pwm1, relay0
int8_t gPinPwm0 = PIN_PWM0_DEFAULT, gPinPwm1 = PIN_PWM1_DEFAULT, gPinRelay0 = PIN_RELAY0_DEFAULT;

// Loop timing - read_telemetry reports it so an agent can see a blocked loop.
uint32_t gLoopUsAvg = 0, gLoopUsMax = 0, gLoopCount = 0;
uint32_t gMcpCalls = 0;
extern uint32_t gPendingSleepS;

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
  logln("server     = resolved at boot (NVS -> mDNS " SERVER_MDNS_HOST ".local -> fallback)");
}

// ---------------------------------------------------------------------------
// Server discovery
// NVS override -> mDNS -> compiled fallback. Called at boot and again after a
// run of failed transmissions, so the fleet recovers on its own if the server
// moves rather than needing 1000 boards reflashed.
// ---------------------------------------------------------------------------
void resolveServer() {
  // 1. host written by the flasher ("server"), 2. legacy "server_ip" pin
  String pinned = gCfg.server;
  if (pinned.length() == 0) {
    prefs.begin("ionity", true);
    pinned = prefs.getString("server_ip", "");
    prefs.end();
  }
  if (pinned.endsWith(".local")) {                // a name, not an address
    if (WiFi.status() == WL_CONNECTED) {
      String h = pinned.substring(0, pinned.length() - 6);
      IPAddress ip = MDNS.queryHost(h.c_str(), 3000);
      if (ip != IPAddress((uint32_t)0)) { gServerHost = ip.toString(); gServerVia = "mdns"; return; }
    }
    pinned = "";
  } else if (pinned.length() > 0 && !IPAddress().fromString(pinned)) {
    IPAddress ip;                                 // plain DNS name
    if (WiFi.status() == WL_CONNECTED && WiFi.hostByName(pinned.c_str(), ip)) {
      gServerHost = ip.toString(); gServerVia = "dns"; return;
    }
  }
  if (pinned.length() > 0) {
    gServerHost = pinned;
    gServerVia  = "nvs";
    logln("server (pinned in NVS): " + gServerHost);
    return;
  }

  if (WiFi.status() == WL_CONNECTED) {
    IPAddress ip = MDNS.queryHost(SERVER_MDNS_HOST, 3000);
    if (ip != IPAddress((uint32_t)0)) {
      gServerHost = ip.toString();
      gServerVia  = "mdns";
      logln("server (mDNS " SERVER_MDNS_HOST ".local): " + gServerHost);
      return;
    }
    logln("mDNS lookup for " SERVER_MDNS_HOST ".local found nothing");
  }

  gServerHost = SERVER_HOST_FALLBACK;
  gServerVia  = "fallback";
  logln("server (compiled fallback): " + gServerHost);
}

// ---------------------------------------------------------------------------
// Sensors
// Swap in your real transducers here. Anything you add under "metrics" in the
// payload is stored, charted and MCP-queryable with NO server change.
// ---------------------------------------------------------------------------
void sampleSensors() {
  gLatest.ts_ms         = millis();
  gLatest.digital_state = gUseDigitalSense ? (digitalRead(PIN_DIGITAL_SENSE) == HIGH) : false;
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

  if (gUseAlertLed) digitalWrite(PIN_LED_ALERT, gLatest.digital_state ? LOW : HIGH);
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
  if (gUseDigitalSense)          m["digital_state"]   = s.digital_state ? 1 : 0;
  if (!isnan(s.rssi_dbm))        m["rssi_dbm"]        = s.rssi_dbm;
  m["oled"]            = oledPresent() ? 1 : 0;
  m["free_heap_bytes"] = s.free_heap;
  m["min_free_heap_bytes"] = ESP.getMinFreeHeap();
  m["loop_us_avg"]     = gLoopUsAvg;
  m["loop_us_max"]     = gLoopUsMax;
  m["state_mode"]      = (int)gMode;
  if (gMode == MODE_INFERENCE_ACTIVE && gLastInference.ok) {
    m["inf_confidence"] = round(gLastInference.confidence * 1000) / 1000.0;
    m["inf_score"]      = round(gLastInference.score * 1000) / 1000.0;
    m["inf_positive"]   = (gLastInference.label == "anomaly" || gLastInference.label == "motion" ||
                           gLastInference.label == "above") ? 1 : 0;
  }
  if (!isnan(s.latency_ms))      m["latency_ms"]      = round(s.latency_ms * 10) / 10.0;
  if (!isnan(s.packet_loss_pct)) m["packet_loss_pct"] = s.packet_loss_pct;

  JsonObject n = doc["net"].to<JsonObject>();
  n["ip"]          = WiFi.localIP().toString();
  n["transport"]   = gUseHttp ? "http" : "mqtt";
  n["server"]      = gServerHost;   // makes a misrouted fleet visible,
  n["resolved_by"] = gServerVia;    // instead of silently dead
  n["mcp"]         = gMcpHttpUp ? ("http://" + WiFi.localIP().toString() + MCP_HTTP_PATH) : "";
  doc["mode"]      = modeName(gMode);

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
  doc["oled"]      = oledNote();
  doc["mode"]      = modeName(gMode);
  doc["mcp"]       = gMcpHttpUp;
  String out;
  serializeJson(doc, out);
  return out;
}

// ---------------------------------------------------------------------------
// DNS probe: one raw A query over UDP to ONE resolver. No system resolver, no
// cache, no fallback server - so the answer is exactly what that resolver (the
// Gate^Flame box) said, which is the whole point of the test.
// Returns a dotted address, "0.0.0.0" when a blocker answers with the null
// address, or NXDOMAIN / SERVFAIL / REFUSED / NOANSWER / TIMEOUT / BADNAME.
// ---------------------------------------------------------------------------
#include <WiFiUdp.h>
String dnsProbe(const IPAddress &server, const String &name, uint32_t &elapsedMs) {
  uint8_t q[300];
  uint16_t id = (uint16_t)esp_random();
  size_t len = 0;
  q[len++] = id >> 8; q[len++] = id & 0xff;
  q[len++] = 0x01; q[len++] = 0x00;            // RD
  q[len++] = 0; q[len++] = 1;                  // QDCOUNT 1
  for (int i = 0; i < 6; i++) q[len++] = 0;    // AN/NS/AR = 0
  int start = 0;
  while (start <= (int)name.length()) {
    int dot = name.indexOf('.', start);
    if (dot < 0) dot = name.length();
    int lab = dot - start;
    if (lab <= 0 || lab > 63 || len + lab + 6 > sizeof(q)) return "BADNAME";
    q[len++] = lab;
    for (int i = start; i < dot; i++) q[len++] = name[i];
    start = dot + 1;
  }
  q[len++] = 0;
  q[len++] = 0; q[len++] = 1;                  // QTYPE A
  q[len++] = 0; q[len++] = 1;                  // QCLASS IN

  WiFiUDP udp;
  udp.begin(0);
  uint32_t t0 = millis();
  udp.beginPacket(server, 53); udp.write(q, len); udp.endPacket();
  uint8_t r[512];
  int got = 0;
  while (millis() - t0 < DNS_PROBE_TIMEOUT_MS) {
    if (udp.parsePacket() > 0) {
      got = udp.read(r, sizeof(r));
      if (got >= 12 && r[0] == q[0] && r[1] == q[1]) break;
      got = 0;
    }
    delay(2);
  }
  elapsedMs = millis() - t0;
  udp.stop();
  if (got < 12) return "TIMEOUT";

  uint8_t rcode = r[3] & 0x0f;
  if (rcode == 3) return "NXDOMAIN";
  if (rcode == 2) return "SERVFAIL";
  if (rcode == 5) return "REFUSED";
  if (rcode != 0) return "RCODE" + String(rcode);
  uint16_t an = (r[6] << 8) | r[7];
  // skip the question section
  int p = 12;
  while (p < got && r[p] != 0) { if ((r[p] & 0xc0) == 0xc0) { p += 1; break; } p += r[p] + 1; }
  p += 1 + 4;
  for (uint16_t i = 0; i < an && p + 10 < got; i++) {
    if ((r[p] & 0xc0) == 0xc0) p += 2; else { while (p < got && r[p] != 0) p += r[p] + 1; p += 1; }
    uint16_t type = (r[p] << 8) | r[p + 1];
    uint16_t rdlen = (r[p + 8] << 8) | r[p + 9];
    p += 10;
    if (type == 1 && rdlen == 4 && p + 4 <= got)
      return String(r[p]) + "." + String(r[p + 1]) + "." + String(r[p + 2]) + "." + String(r[p + 3]);
    p += rdlen;                                 // CNAME etc. - keep walking
  }
  return "NOANSWER";
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
    if (gUseHeartbeatLed) {
      for (int i = 0; i < 10; i++) {
        digitalWrite(PIN_LED_HEARTBEAT, !digitalRead(PIN_LED_HEARTBEAT));
        delay(120);
      }
    }
    oledIdentify();
    publishCmdResult(cmdId, true, oledPresent() ? "blinked LED + flashed display" : "blinked");
  } else if (action == "set_display") {
    // {"driver":"ssd1306"|"sh1106"|"off"|"auto", "sda":N, "scl":N}
    // "auto" clears pinned pins so the next boot scans again.
    prefs.begin("ionity", false);
    String drv = doc["driver"] | "";
    if (drv == "auto") { prefs.remove("oled_sda"); prefs.remove("oled_scl"); prefs.remove("oled_none"); prefs.putString("oled_drv", "ssd1306"); }
    else if (drv.length()) prefs.putString("oled_drv", drv);
    if (doc["sda"].is<int>() && doc["scl"].is<int>()) {
      prefs.putInt("oled_sda", doc["sda"].as<int>());
      prefs.putInt("oled_scl", doc["scl"].as<int>());
    }
    prefs.end();
    publishCmdResult(cmdId, true, "display settings stored; rebooting");
    delay(250);
    ESP.restart();
  } else if (action == "set_meta") {
    if (doc["site"].is<const char*>())  { gSite  = doc["site"].as<String>();  persistIdentity("site", gSite); }
    if (doc["group"].is<const char*>()) { gGroup = doc["group"].as<String>(); persistIdentity("group", gGroup); }
    if (doc["label"].is<const char*>()) { gLabel = doc["label"].as<String>(); persistIdentity("label", gLabel); }
    publishCmdResult(cmdId, true, "metadata stored; rebooting to re-topic");
    delay(250);
    ESP.restart();
  } else if (action == "mcp") {
    // {"action":"mcp","cmd_id":"..","rpc":{"jsonrpc":"2.0","id":1,"method":"tools/call",...}}
    // The fleet host bridges an AI agent's call to this board's own MCP tools.
    // MQTT is trusted (the host enforces its admin token), so write tools are allowed.
    JsonDocument resp;
    mcpDispatch(doc["rpc"].as<JsonObject>(), resp, true);
    String out; serializeJson(resp, out);
    publishCmdResult(cmdId, !resp["error"].is<JsonObject>(), out);
  } else if (action == "set_state_mode") {
    String mode = doc["mode"] | "";
    String upper = mode; upper.toUpperCase();
    if (upper == "LOW_POWER_SLEEP") {           // reply first, then sleep from loop()
      publishCmdResult(cmdId, true, "entering LOW_POWER_SLEEP");
      gPendingSleepS = doc["duration_s"] | DEFAULT_SLEEP_S;
      return;
    }
    String err;
    bool ok = applyStateMode(mode, 0, err, cmdId);
    publishCmdResult(cmdId, ok, ok ? String("mode ") + modeName(gMode) : err);
  } else if (action == "ping") {
    publishCmdResult(cmdId, true, "pong");
  } else if (action == "dns_probe") {
    // {"names":["doubleclick.net","ionity.today"], "dns_server":"192.168.124.3"}
    String server = doc["dns_server"] | "";
    if (server.length() == 0) {
      prefs.begin("ionity", true);
      server = prefs.getString("gf_dns", GF_DNS_DEFAULT);
      prefs.end();
    } else if (doc["remember"] | false) {
      prefs.begin("ionity", false); prefs.putString("gf_dns", server); prefs.end();
    }
    IPAddress srv;
    if (!srv.fromString(server)) { publishCmdResult(cmdId, false, "dns_server is not an IPv4 address"); return; }
    if (WiFi.status() != WL_CONNECTED) { publishCmdResult(cmdId, false, "WiFi not connected"); return; }

    JsonDocument res;
    res["server"] = server;
    res["from"]   = WiFi.localIP().toString();
    JsonArray arr = res["answers"].to<JsonArray>();
    int blocked = 0, resolved = 0, n = 0;
    for (JsonVariant v : doc["names"].as<JsonArray>()) {
      if (n++ >= DNS_PROBE_MAX_NAMES) break;
      String name = v.as<String>();
      uint32_t ms = 0;
      String ans = dnsProbe(srv, name, ms);
      JsonObject o = arr.add<JsonObject>();
      o["n"] = name; o["a"] = ans; o["ms"] = ms;
      if (ans == "0.0.0.0" || ans == "::") blocked++;
      else if (ans[0] >= '0' && ans[0] <= '9') resolved++;
      logln("dns_probe " + name + " @" + server + " -> " + ans + " (" + String(ms) + "ms)");
    }
    res["blocked"] = blocked; res["resolved"] = resolved; res["asked"] = arr.size();
    String out; serializeJson(res, out);
    publishCmdResult(cmdId, true, out);
  } else {
    publishCmdResult(cmdId, false, "unknown action");
  }
}

// ---------------------------------------------------------------------------
// Connectivity
// ---------------------------------------------------------------------------
void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (!gWifiKick && millis() - lastWifiTry < WIFI_RETRY_MS) return;
  gWifiKick = false;
  lastWifiTry = millis();

  if (gCfg.wifiSsid.length() == 0) return;          // not provisioned yet
  logln("WiFi connecting to \"" + gCfg.wifiSsid + "\" ...");
  WiFi.mode(WIFI_STA);
  WiFi.setHostname((String(OTA_HOSTNAME_PREFIX) + macSuffix()).c_str());
  WiFi.setAutoReconnect(true);
  WiFi.begin(gCfg.wifiSsid.c_str(), gCfg.wifiPass.c_str());
}

bool ensureMqtt() {
  if (mqtt.connected()) { gMqttFails = 0; gUseHttp = false; return true; }
  if (gCfg.role == "standalone") return false;       // MCP-only board, no host
  if (WiFi.status() != WL_CONNECTED) return false;
  // On the HTTP fallback, probe the broker only once a minute: each failed
  // connect() blocks ~3 s, which would starve serial provisioning and /mcp.
  if (millis() - lastMqttTry < (gUseHttp ? 60000UL : (unsigned long)MQTT_RETRY_MS)) return false;
  lastMqttTry = millis();

  mqtt.setServer(gServerHost.c_str(), gCfg.mqttPort);
  mqtt.setKeepAlive(MQTT_KEEPALIVE_S);
  mqtt.setBufferSize(4096);   // tools/list via MQTT is ~2.5 KB once escaped
  mqtt.setCallback(onMqttMessage);

  String willPayload = buildStatusJson("offline");
  bool ok = (gCfg.mqttUser.length() > 0)
    ? mqtt.connect(gDeviceId.c_str(), gCfg.mqttUser.c_str(), gCfg.mqttPass.c_str(),
                   tStatus.c_str(), 1, true, willPayload.c_str())
    : mqtt.connect(gDeviceId.c_str(), NULL, NULL,
                   tStatus.c_str(), 1, true, willPayload.c_str());

  if (ok) {
    logln("MQTT connected -> " + gServerHost);
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
  if (gCfg.role == "standalone") return false;
  String url = "http://" + gServerHost + ":" + String(gCfg.httpPort) + HTTP_INGEST_PATH;
  http.setConnectTimeout(4000);
  http.setTimeout(6000);
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Fleet-Token", gCfg.fleetToken);
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
  if (ok) {
    gTxOk++;
    gConsecutiveFails = 0;
  } else {
    gTxFail++;
    // A run of failures usually means the server moved (new router, new
    // subnet, DHCP reshuffle). Re-resolve rather than hammering a dead IP.
    if (++gConsecutiveFails >= RESOLVE_RETRY_AFTER) {
      gConsecutiveFails = 0;
      logln("repeated transmit failures - re-resolving server address");
      resolveServer();
      if (mqtt.connected()) mqtt.disconnect();
      gUseHttp = false;
      gMqttFails = 0;
    }
  }
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
    if (gUseHeartbeatLed) {
      digitalWrite(PIN_LED_HEARTBEAT, HIGH); delay(25);
      digitalWrite(PIN_LED_HEARTBEAT, LOW);
    }
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
  if (gCfg.otaPass.length() == 0) { logln("OTA off (no ota_pass provisioned)"); return; }
  ArduinoOTA.setPassword(gCfg.otaPass.c_str());
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
bool gNetInit = false, gWasUp = false;

void onWifiUp() {
  gWasUp = true;
  logln("WiFi OK   ip=" + WiFi.localIP().toString() +
        "  gw=" + WiFi.gatewayIP().toString() +
        "  rssi=" + String(WiFi.RSSI()) + "dBm");
  if (!gNetInit) {                 // once per boot; WiFi can drop and return
    gNetInit = true;
    MDNS.begin((String(OTA_HOSTNAME_PREFIX) + macSuffix()).c_str());
    MDNS.addService("ionity-mcp", "tcp", MCP_HTTP_PORT);
#if OTA_ENABLED
    setupOta();
#endif
    mcpHttpBegin();
  }
  resolveServer();
}

void setup() {
  Serial.setRxBufferSize(1024);    // before begin(): core 3.x ignores it afterwards
  Serial.begin(SERIAL_BAUD);
  delay(1200);                      // let native-USB CDC enumerate
  Serial.println();
  logln("=========================================================");
  logln(" IONITY ESP32-MCP FLEET NODE  |  fw " FW_VERSION);
  logln(" (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd");
  logln(" Policy 986 AED | Building Tomorrow, Today.");
  logln("=========================================================");

  gLatest.latency_ms      = NAN;
  gLatest.packet_loss_pct = NAN;
  gLatest.rssi_dbm        = NAN;

  loadIdentity();
  loadConfig();                     // Provision tab: NVS, seeded once from secrets.h
  loadStateMode();                  // EdgeAI tab: FAILSAFE survives a reboot
  loadActuatorPins();

  oledDetect();
  if (gUseHeartbeatLed) { pinMode(PIN_LED_HEARTBEAT, OUTPUT); digitalWrite(PIN_LED_HEARTBEAT, LOW); }
  if (gUseAlertLed)     { pinMode(PIN_LED_ALERT, OUTPUT);     digitalWrite(PIN_LED_ALERT, LOW); }
  if (gUseDigitalSense) pinMode(PIN_DIGITAL_SENSE, INPUT_PULLUP);

  provAnnounce();                   // "IONITY-PROV {hello}" - the flasher waits for this

  if (gCfg.wifiSsid.length() == 0) {
    // Unprovisioned: stay reachable on serial until the flasher writes WiFi.
    logln("NOT PROVISIONED - waiting for WiFi + MCP host over USB serial");
    unsigned long lastLog = millis();
    while (gCfg.wifiSsid.length() == 0) {
      provPoll();
      if (millis() - lastLog > PROV_WAIT_LOG_MS) { lastLog = millis(); provAnnounce(); }
      if (gUseHeartbeatLed) digitalWrite(PIN_LED_HEARTBEAT, (millis() / 500) % 2);
      delay(20);
    }
  }

  ensureWifi();
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    provPoll();                     // the flasher can fix a wrong password here
    ensureWifi();                   // retries, and picks up credentials the flasher just wrote
    delay(50);
    if (gUseHeartbeatLed) digitalWrite(PIN_LED_HEARTBEAT, (millis() / 250) % 2);
  }
  if (gUseHeartbeatLed) digitalWrite(PIN_LED_HEARTBEAT, LOW);

  if (WiFi.status() == WL_CONNECTED) {
    onWifiUp();
  } else {
    logln("WiFi FAILED for \"" + gCfg.wifiSsid + "\" - re-provision with the flasher. Buffering locally.");
    if (gUseAlertLed) digitalWrite(PIN_LED_ALERT, HIGH);
    resolveServer();
  }

  ensureMqtt();
  sampleSensors();
  logln("Boot complete. mode=" + String(modeName(gMode)) + "  host " + gServerHost + " (via " +
        gServerVia + ")  role=" + gCfg.role);
}

void loop() {
  uint32_t t0 = micros();
  unsigned long now = millis();

  provPoll();

  ensureWifi();
  bool up = (WiFi.status() == WL_CONNECTED);
  if (up && !gWasUp) onWifiUp();                    // (re)joined after boot
  if (!up) gWasUp = false;

  ensureMqtt();
  if (mqtt.connected()) mqtt.loop();
  if (gMcpHttpUp) mcpHttp.handleClient();
  mcpAfterReply();
#if OTA_ENABLED
  ArduinoOTA.handle();
#endif

  if (now - lastFast >= FAST_SAMPLE_MS) {
    lastFast = now;
    gRingAnalog.push((analogRead(PIN_ANALOG_SENSE) / 4095.0f) * 3.3f);
    if (up) gRingRssi.push((float)WiFi.RSSI());
  }
  if (now - lastSample >= SENSOR_SAMPLE_MS) {
    lastSample = now;
    sampleSensors();
    if (gMode == MODE_INFERENCE_ACTIVE) edgeTick();
  }
  oledUpdate();

  uint32_t every = (gMode == MODE_STANDBY) ? STANDBY_TELEMETRY_MS : TELEMETRY_INTERVAL_MS;
  if (now - lastTelemetry >= every) {
    lastTelemetry = now;
    pushTelemetry();
    gLoopUsMax = 0;                                 // window max per report
  }

  if (now - lastStatus >= HEARTBEAT_STATUS_MS) {
    lastStatus = now;
    if (mqtt.connected())
      mqtt.publish(tStatus.c_str(), buildStatusJson("online").c_str(), true);
  }

  uint32_t dt = micros() - t0;
  gLoopUsAvg = gLoopCount++ ? (gLoopUsAvg * 15 + dt) / 16 : dt;
  if (dt > gLoopUsMax) gLoopUsMax = dt;
  delay(gMode == MODE_STANDBY ? 40 : 10);
}
