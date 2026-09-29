// ===========================================================================
// AEDI - IONITY GLOBAL | NVS configuration + USB-serial provisioning
// Doc ID: DOC-2026-09-ESP32MCP-PROV | Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
// ---------------------------------------------------------------------------
// Why: 1.x compiled WiFi into secrets.h, so every network change meant a
// rebuild and a reflash of every board. 2.0 keeps WiFi, the MCP host and the
// tokens in NVS and takes them over the same USB cable the flasher used.
//
// Protocol (115200 8N1, one JSON object per line, either direction):
//   host  -> {"ionity":"prov","op":"hello"}
//   board -> IONITY-PROV {"ok":true,"op":"hello","device_id":"esp32-..",...}
//
//   ops: hello | get | set | scan | test | reboot | factory_reset
//   set fields (all optional): ssid, pass, server, mqtt_port, http_port,
//     mqtt_user, mqtt_pass, fleet_token, mcp_token, ota_pass, role,
//     site, group, label, pins{pwm0,pwm1,relay0}
//   "set" never echoes secrets back; "get" reports has_pass / has_token only.
//   "test" joins the WiFi and resolves the host without rebooting, so the
//   flasher can show "connected, 192.168.0.42, host found" before it leaves.
//
// Everything else the board prints is ordinary log text; the flasher only
// parses lines that start with PROV_PREFIX.
// ===========================================================================

static char   provBuf[PROV_LINE_MAX];
static size_t provLen = 0;

void loadConfig() {
  prefs.begin("ionity", false);
  // First boot of an image built with a secrets.h: seed NVS from it once.
  if (!prefs.getBool("seeded", false)) {
    if (strlen(WIFI_SSID) && !prefs.isKey("wifi_ssid")) {
      prefs.putString("wifi_ssid", WIFI_SSID);
      prefs.putString("wifi_pass", WIFI_PASSWORD);
    }
    if (!prefs.isKey("fleet_token")) prefs.putString("fleet_token", FLEET_TOKEN);
    if (strlen(MQTT_USERNAME) && !prefs.isKey("mqtt_user")) {
      prefs.putString("mqtt_user", MQTT_USERNAME);
      prefs.putString("mqtt_pass", MQTT_PASSWORD);
    }
    if (strlen(OTA_PASSWORD) && !prefs.isKey("ota_pass")) prefs.putString("ota_pass", OTA_PASSWORD);
    if (strlen(MCP_TOKEN) && !prefs.isKey("mcp_token"))   prefs.putString("mcp_token", MCP_TOKEN);
    prefs.putBool("seeded", true);
  }
  gCfg.wifiSsid   = prefs.getString("wifi_ssid", "");
  gCfg.wifiPass   = prefs.getString("wifi_pass", "");
  gCfg.server     = prefs.getString("server", "");
  gCfg.mqttPort   = prefs.getUShort("mqtt_port", MQTT_PORT);
  gCfg.httpPort   = prefs.getUShort("http_port", SERVER_HTTP_PORT);
  gCfg.mqttUser   = prefs.getString("mqtt_user", "");
  gCfg.mqttPass   = prefs.getString("mqtt_pass", "");
  gCfg.fleetToken = prefs.getString("fleet_token", FLEET_TOKEN);
  gCfg.mcpToken   = prefs.getString("mcp_token", "");
  gCfg.otaPass    = prefs.getString("ota_pass", "");
  gCfg.role       = prefs.getString("role", "node");
  prefs.end();
  logln("config: wifi=\"" + gCfg.wifiSsid + "\" host=" +
        (gCfg.server.length() ? gCfg.server : String("mdns:" SERVER_MDNS_HOST ".local")) +
        " role=" + gCfg.role + " mcp_token=" + (gCfg.mcpToken.length() ? "set" : "none"));
}

static void provReply(JsonDocument &d) {
  String out; serializeJson(d, out);
  Serial.print(PROV_PREFIX);
  Serial.println(out);
}

static void provError(const char *op, const String &msg) {
  JsonDocument d;
  d["ok"] = false; d["op"] = op; d["error"] = msg;
  provReply(d);
}

static void provFillInfo(JsonDocument &d) {
  d["device_id"]  = gDeviceId;
  d["fw"]         = FW_VERSION;
  d["product"]    = FW_PRODUCT;
  d["chip"]       = ESP.getChipModel();
  d["chip_rev"]   = ESP.getChipRevision();
  d["flash_mb"]   = ESP.getFlashChipSize() / (1024 * 1024);
  uint8_t mac[6]; esp_read_mac(mac, ESP_MAC_WIFI_STA);          // valid before WiFi starts
  char macs[18]; snprintf(macs, sizeof(macs), "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  d["mac"]        = macs;
  d["provisioned"] = gCfg.wifiSsid.length() > 0;
  d["ssid"]       = gCfg.wifiSsid;
  d["has_pass"]   = gCfg.wifiPass.length() > 0;
  d["server"]     = gCfg.server;
  d["mqtt_port"]  = gCfg.mqttPort;
  d["http_port"]  = gCfg.httpPort;
  d["role"]       = gCfg.role;
  d["site"]       = gSite;
  d["group"]      = gGroup;
  d["label"]      = gLabel;
  d["has_mcp_token"] = gCfg.mcpToken.length() > 0;
  d["has_ota_pass"]  = gCfg.otaPass.length() > 0;
  d["mode"]       = modeName(gMode);
  bool up = WiFi.status() == WL_CONNECTED;
  d["wifi"]       = up ? "connected" : "down";
  if (!up && gWifiReason) { d["wifi_reason"] = gWifiReason; d["wifi_error"] = wifiReasonText(gWifiReason); }
  if (up) {
    d["ip"]   = WiFi.localIP().toString();
    d["rssi"] = WiFi.RSSI();
    d["mcp_url"] = gMcpHttpUp ? ("http://" + WiFi.localIP().toString() + MCP_HTTP_PATH) : "";
  }
  d["host_resolved"] = gServerHost;
  d["host_via"]      = gServerVia;
  d["mqtt"]          = mqtt.connected();
}

void provAnnounce() {
  JsonDocument d;
  d["ok"] = true; d["op"] = "hello";
  provFillInfo(d);
  provReply(d);
}

static bool putStr(JsonVariantConst v, const char *key, String &field) {
  if (!v.is<const char*>()) return false;
  field = v.as<String>();
  prefs.putString(key, field);
  return true;
}

static void provSet(JsonObjectConst in) {
  JsonDocument d;
  d["ok"] = true; d["op"] = "set";
  JsonArray changed = d["changed"].to<JsonArray>();
  bool netChanged = false, reTopic = false;

  prefs.begin("ionity", false);
  if (in["ssid"].is<const char*>()) {
    String s = in["ssid"].as<String>();
    if (s.length() == 0 || s.length() > 32) { prefs.end(); provError("set", "ssid must be 1-32 chars"); return; }
    gCfg.wifiSsid = s; prefs.putString("wifi_ssid", s); changed.add("ssid"); netChanged = true;
  }
  if (in["pass"].is<const char*>()) {
    String p = in["pass"].as<String>();
    if (p.length() > 0 && (p.length() < 8 || p.length() > 63)) { prefs.end(); provError("set", "WPA2 password must be 8-63 chars (or empty for open)"); return; }
    gCfg.wifiPass = p; prefs.putString("wifi_pass", p); changed.add("pass"); netChanged = true;
  }
  if (putStr(in["server"], "server", gCfg.server))           changed.add("server");
  if (in["mqtt_port"].is<int>()) { gCfg.mqttPort = in["mqtt_port"].as<int>(); prefs.putUShort("mqtt_port", gCfg.mqttPort); changed.add("mqtt_port"); }
  if (in["http_port"].is<int>()) { gCfg.httpPort = in["http_port"].as<int>(); prefs.putUShort("http_port", gCfg.httpPort); changed.add("http_port"); }
  if (putStr(in["mqtt_user"],   "mqtt_user",   gCfg.mqttUser))   changed.add("mqtt_user");
  if (putStr(in["mqtt_pass"],   "mqtt_pass",   gCfg.mqttPass))   changed.add("mqtt_pass");
  if (putStr(in["fleet_token"], "fleet_token", gCfg.fleetToken)) changed.add("fleet_token");
  if (putStr(in["mcp_token"],   "mcp_token",   gCfg.mcpToken))   changed.add("mcp_token");
  if (putStr(in["ota_pass"],    "ota_pass",    gCfg.otaPass))    changed.add("ota_pass");
  if (in["role"].is<const char*>()) {
    String r = in["role"].as<String>();
    if (r != "node" && r != "standalone") { prefs.end(); provError("set", "role must be node|standalone"); return; }
    gCfg.role = r; prefs.putString("role", r); changed.add("role");
  }
  if (putStr(in["site"],  "site",  gSite))  { changed.add("site");  reTopic = true; }
  if (putStr(in["group"], "group", gGroup)) { changed.add("group"); }
  if (putStr(in["label"], "label", gLabel)) { changed.add("label"); }
  if (in["pins"].is<JsonObjectConst>()) {
    JsonObjectConst p = in["pins"];
    if (p["pwm0"].is<int>())   prefs.putInt("pin_pwm0",   p["pwm0"].as<int>());
    if (p["pwm1"].is<int>())   prefs.putInt("pin_pwm1",   p["pwm1"].as<int>());
    if (p["relay0"].is<int>()) prefs.putInt("pin_relay0", p["relay0"].as<int>());
    changed.add("pins");
  }
  prefs.end();

  d["reboot_required"] = reTopic || changed.size() > 0;
  provFillInfo(d);
  provReply(d);
  if (netChanged) {                  // pick up new WiFi straight away
    WiFi.disconnect();
    gWifiKick = true;
  }
}

static void provScan() {
  WiFi.mode(WIFI_STA);
  // A scan while the STA is still trying to associate returns -2 (busy).
  if (WiFi.status() != WL_CONNECTED) { WiFi.disconnect(false, false); delay(200); }
  int n = WiFi.scanNetworks(false, true);                     // include hidden SSIDs
  if (n == WIFI_SCAN_FAILED || n == WIFI_SCAN_RUNNING) { delay(300); n = WiFi.scanNetworks(false, false); }
  gWifiKick = true;                  // resume joining afterwards
  JsonDocument d;
  d["ok"] = n >= 0; d["op"] = "scan";
  if (n < 0) d["error"] = "scan failed (radio busy) - try again";
  JsonArray arr = d["networks"].to<JsonArray>();
  for (int i = 0; i < n && i < 20; i++) {
    JsonObject o = arr.add<JsonObject>();
    o["ssid"] = WiFi.SSID(i);
    o["rssi"] = WiFi.RSSI(i);
    o["ch"]   = WiFi.channel(i);
    o["open"] = WiFi.encryptionType(i) == WIFI_AUTH_OPEN;
  }
  WiFi.scanDelete();
  provReply(d);
}

static void provTest(uint32_t timeoutMs) {
  if (gCfg.wifiSsid.length() == 0) { provError("test", "no ssid provisioned"); return; }
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.mode(WIFI_STA);
    WiFi.disconnect(false, false);
    delay(50);
    WiFi.begin(gCfg.wifiSsid.c_str(), gCfg.wifiPass.c_str());
    unsigned long t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < timeoutMs) delay(100);
  }
  JsonDocument d;
  d["op"] = "test";
  bool up = WiFi.status() == WL_CONNECTED;
  d["ok"] = up;
  if (!up) {
    wl_status_t st = WiFi.status();
    d["error"] = gWifiReason ? String(wifiReasonText(gWifiReason)) + " (reason " + String(gWifiReason) + ")" :
                 (st == WL_NO_SSID_AVAIL) ? String("ssid not found (2.4 GHz only)") :
                 (st == WL_CONNECT_FAILED) ? String("wrong password or rejected") : String("timeout joining WiFi");
    provReply(d);
    return;
  }
  onWifiUp();                        // mDNS, MCP HTTP, resolve host
  lastMqttTry = 0;
  bool mq = ensureMqtt() || mqtt.connected();
  provFillInfo(d);
  d["mqtt"] = mq;
  provReply(d);
}

static void provHandle(const char *line) {
  JsonDocument in;
  if (deserializeJson(in, line)) return;               // not for us
  if (strcmp(in["ionity"] | "", "prov") != 0) return;
  String op = in["op"] | "";

  if (op == "hello" || op == "get") {
    JsonDocument d; d["ok"] = true; d["op"] = op; provFillInfo(d); provReply(d);
  } else if (op == "set") {
    provSet(in.as<JsonObjectConst>());
  } else if (op == "scan") {
    provScan();
  } else if (op == "test") {
    provTest(in["timeout_ms"] | 15000);
  } else if (op == "reboot") {
    JsonDocument d; d["ok"] = true; d["op"] = "reboot"; provReply(d);
    Serial.flush(); delay(200); ESP.restart();
  } else if (op == "factory_reset") {
    prefs.begin("ionity", false); prefs.clear(); prefs.end();
    JsonDocument d; d["ok"] = true; d["op"] = "factory_reset"; provReply(d);
    Serial.flush(); delay(200); ESP.restart();
  } else {
    provError(op.c_str(), "unknown op");
  }
}

// Non-blocking: call from loop() and from any wait loop.
void provPoll() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      provBuf[provLen] = 0;
      if (provLen > 0 && provBuf[0] == '{') provHandle(provBuf);
      provLen = 0;
    } else if (provLen < PROV_LINE_MAX - 1) {
      provBuf[provLen++] = c;
    } else {
      provLen = 0;                                      // overlong line: drop it
    }
  }
}
