// ===========================================================================
// AEDI - IONITY GLOBAL | On-device MCP server (Model Context Protocol)
// Doc ID: DOC-2026-09-ESP32MCP-DMCP | Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
// ---------------------------------------------------------------------------
// The board is its own MCP server. Two ways in, one dispatcher:
//
//   1. HTTP  POST http://<board>/mcp      JSON-RPC 2.0, Streamable-HTTP style
//            (JSON responses, no SSE). For agents on the same LAN.
//   2. MQTT  {"action":"mcp","rpc":{...}}  on ionity/<site>/<id>/cmd
//            The fleet host's device_call_tool uses this, so an agent never
//            needs the board's IP and the board never parses heavy handshakes
//            from the internet.
//
// Methods: initialize, notifications/initialized, ping, tools/list, tools/call
// Tools:   get_device_info, read_telemetry, run_inference, set_actuator,
//          set_state_mode, identify
//
// Auth: write tools (set_actuator, set_state_mode, identify) over HTTP need
// "Authorization: Bearer <mcp_token>" (provisioned by the flasher). With no
// token provisioned the HTTP endpoint is read-only. When a token IS set,
// every HTTP request must carry it. MQTT calls are trusted: the host guards
// them with IONITY_ADMIN_TOKEN.
// ===========================================================================

static const char *MCP_VERSIONS[] = {"2025-06-18", "2025-03-26", "2024-11-05"};

static const char MCP_TOOLS_JSON[] PROGMEM = R"JSON([
{"name":"get_device_info","title":"Device info",
 "description":"Identity, chip, firmware, provisioning (no secrets), network, state mode and which tools this board exposes.",
 "inputSchema":{"type":"object","properties":{}},
 "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}},
{"name":"read_telemetry","title":"Read telemetry",
 "description":"Live readings straight from the board: internal temperature, ADC volts, digital input, RSSI, free and minimum-ever heap, largest free block, PSRAM, loop timing (avg/max us), uptime, transport, actuator states and the last inference.",
 "inputSchema":{"type":"object","properties":{}},
 "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}},
{"name":"run_inference","title":"Run edge inference",
 "description":"Run an on-chip model and return label + confidence. anomaly_zscore: newest ADC sample vs the rolling window (|z|>threshold, default 3). rssi_motion: WiFi RSSI jitter as a coarse presence/motion signal (std-dev > threshold dB, default 2.5). analog_threshold: newest ADC volts vs threshold (default 1.65). Pass input_frame to run on your own numbers instead of live data.",
 "inputSchema":{"type":"object","properties":{
   "model_id":{"type":"string","enum":["anomaly_zscore","rssi_motion","analog_threshold"]},
   "input_frame":{"type":"array","items":{"type":"number"},"maxItems":64},
   "threshold":{"type":"number"}},"required":["model_id"]},
 "annotations":{"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}},
{"name":"set_actuator","title":"Set actuator",
 "description":"Drive an output. led / alert_led / relay0: 0 or 1. pwm0 / pwm1: duty 0.0-1.0 (5 kHz). Refused in FAILSAFE mode.",
 "inputSchema":{"type":"object","properties":{
   "channel":{"type":"string","enum":["led","alert_led","pwm0","pwm1","relay0"]},
   "value":{"type":"number","minimum":0,"maximum":1}},"required":["channel","value"]},
 "annotations":{"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}},
{"name":"set_state_mode","title":"Set state mode",
 "description":"ACTIVE (10 s telemetry), STANDBY (60 s, no inference), INFERENCE_ACTIVE (runs model_id every 2 s and reports inf_* metrics + events), LOW_POWER_SLEEP (deep sleep for duration_s, default 300, then wakes ACTIVE; the board is unreachable meanwhile), FAILSAFE (all actuators off and locked; survives reboot).",
 "inputSchema":{"type":"object","properties":{
   "mode":{"type":"string","enum":["STANDBY","ACTIVE","INFERENCE_ACTIVE","LOW_POWER_SLEEP","FAILSAFE"]},
   "duration_s":{"type":"integer","minimum":5,"maximum":86400},
   "model_id":{"type":"string","enum":["anomaly_zscore","rssi_motion","analog_threshold"]}},"required":["mode"]},
 "annotations":{"readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":true}},
{"name":"identify","title":"Identify board",
 "description":"Blink the LED and flash the OLED so a human can find this board on the bench.",
 "inputSchema":{"type":"object","properties":{}},
 "annotations":{"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}}
])JSON";

static bool toolIsWrite(const String &n) {
  return n == "set_actuator" || n == "set_state_mode" || n == "identify";
}

static void rpcError(JsonDocument &resp, JsonVariantConst id, int code, const String &msg) {
  resp.clear();
  resp["jsonrpc"] = "2.0";
  resp["id"] = id;
  JsonObject e = resp["error"].to<JsonObject>();
  e["code"] = code;
  e["message"] = msg;
}

static void toolResult(JsonDocument &resp, JsonVariantConst id, JsonDocument &data, bool isError) {
  resp.clear();
  resp["jsonrpc"] = "2.0";
  resp["id"] = id;
  JsonObject r = resp["result"].to<JsonObject>();
  String text; serializeJson(data, text);
  JsonObject c = r["content"].to<JsonArray>().add<JsonObject>();
  c["type"] = "text";
  c["text"] = text;
  if (!isError) r["structuredContent"] = data;
  r["isError"] = isError;
}

static void fillTelemetry(JsonDocument &d) {
  d["device_id"] = gDeviceId;
  d["fw"] = FW_VERSION;
  d["uptime_s"] = millis() / 1000;
  d["mode"] = modeName(gMode);
  JsonObject m = d["metrics"].to<JsonObject>();
  if (!isnan(gLatest.temp_c)) m["temp_c"] = round(gLatest.temp_c * 10) / 10.0;
  m["analog_v"] = round(gLatest.analog_v * 1000) / 1000.0;
  if (gUseDigitalSense) m["digital_state"] = gLatest.digital_state ? 1 : 0;
  if (WiFi.status() == WL_CONNECTED) m["rssi_dbm"] = WiFi.RSSI();
  m["free_heap_bytes"] = ESP.getFreeHeap();
  m["min_free_heap_bytes"] = ESP.getMinFreeHeap();
  m["max_alloc_heap_bytes"] = ESP.getMaxAllocHeap();
  if (ESP.getPsramSize()) { m["psram_free_bytes"] = ESP.getFreePsram(); m["psram_total_bytes"] = ESP.getPsramSize(); }
  m["loop_us_avg"] = gLoopUsAvg;
  m["loop_us_max"] = gLoopUsMax;
  m["freertos_tasks"] = uxTaskGetNumberOfTasks();
  m["loop_stack_free_bytes"] = uxTaskGetStackHighWaterMark(NULL);
  JsonObject n = d["net"].to<JsonObject>();
  n["wifi"] = WiFi.status() == WL_CONNECTED ? "connected" : "down";
  n["ip"] = WiFi.localIP().toString();
  n["transport"] = gUseHttp ? "http" : (mqtt.connected() ? "mqtt" : "none");
  n["host"] = gServerHost;
  n["tx_ok"] = gTxOk; n["tx_fail"] = gTxFail; n["buffered"] = gBufCount;
  actuatorsToJson(d["actuators"].to<JsonObject>());
  if (gLastInference.ok) {
    JsonObject i = d["last_inference"].to<JsonObject>();
    i["model"] = gLastInference.model; i["label"] = gLastInference.label;
    i["confidence"] = gLastInference.confidence; i["score"] = gLastInference.score;
  }
  d["inference_runs"] = gInferenceRuns;
  d["mcp_calls"] = gMcpCalls;
}

static void fillInfo(JsonDocument &d) {
  d["device_id"] = gDeviceId;
  d["label"] = gLabel; d["site"] = gSite; d["group"] = gGroup;
  d["fw"] = FW_VERSION; d["product"] = FW_PRODUCT;
  d["chip"] = ESP.getChipModel(); d["chip_rev"] = ESP.getChipRevision();
  d["cores"] = ESP.getChipCores(); d["cpu_mhz"] = ESP.getCpuFreqMHz();
  d["flash_mb"] = ESP.getFlashChipSize() / (1024 * 1024);
  d["psram_bytes"] = ESP.getPsramSize();
  d["sdk"] = ESP.getSdkVersion();
  d["mac"] = WiFi.macAddress();
  d["role"] = gCfg.role;
  d["mode"] = modeName(gMode);
  d["inference_model"] = gInfModel;
  d["host"] = gServerHost; d["host_via"] = gServerVia;
  d["mcp_http"] = gMcpHttpUp ? ("http://" + WiFi.localIP().toString() + MCP_HTTP_PATH) : "";
  d["mcp_http_writes"] = gCfg.mcpToken.length() > 0;
  d["oled"] = oledNote();
  JsonArray t = d["tools"].to<JsonArray>();
  for (const char *n : {"get_device_info", "read_telemetry", "run_inference", "set_actuator", "set_state_mode", "identify"}) t.add(n);
}

static void callTool(const String &name, JsonObjectConst args, JsonVariantConst id,
                     JsonDocument &resp, bool writeAllowed) {
  JsonDocument out;
  if (toolIsWrite(name) && !writeAllowed) {
    out["error"] = "write tools need the board's mcp_token (Authorization: Bearer ...) "
                   "or a call through the fleet host";
    toolResult(resp, id, out, true);
    return;
  }
  if (name == "get_device_info") { fillInfo(out); toolResult(resp, id, out, false); return; }
  if (name == "read_telemetry")  { fillTelemetry(out); toolResult(resp, id, out, false); return; }

  if (name == "run_inference") {
    String model = args["model_id"] | "";
    static float frame[RING_LEN];
    uint16_t n = 0;
    if (args["input_frame"].is<JsonArrayConst>()) {
      for (JsonVariantConst v : args["input_frame"].as<JsonArrayConst>()) {
        if (n >= RING_LEN) break;
        if (!v.is<float>()) { out["error"] = "input_frame must contain only numbers"; toolResult(resp, id, out, true); return; }
        frame[n++] = v.as<float>();
      }
    }
    float thr = args["threshold"].is<float>() ? args["threshold"].as<float>() : NAN;
    Inference r = runInference(model, n ? frame : nullptr, n, thr);
    if (!r.ok) { out["error"] = r.error; out["model_id"] = model; out["samples"] = r.samples; toolResult(resp, id, out, true); return; }
    gLastInference = r;
    out["model_id"] = r.model; out["label"] = r.label;
    out["confidence"] = round(r.confidence * 1000) / 1000.0;
    out["score"] = r.score; out["samples"] = r.samples;
    out["source"] = n ? "input_frame" : "live";
    out["elapsed_us"] = r.elapsed_us;
    toolResult(resp, id, out, false);
    return;
  }

  if (name == "set_actuator") {
    String ch = args["channel"] | "";
    float v = args["value"].is<float>() ? args["value"].as<float>() : NAN;
    String err = setActuator(ch, v);
    if (err.length()) { out["error"] = err; toolResult(resp, id, out, true); return; }
    out["channel"] = ch; out["value"] = gAct[actIndex(ch)];
    out["mode"] = modeName(gMode);
    toolResult(resp, id, out, false);
    return;
  }

  if (name == "set_state_mode") {
    String mode = args["mode"] | "";
    if (args["model_id"].is<const char*>()) {
      gInfModel = args["model_id"].as<String>();
      prefs.begin("ionity", false); prefs.putString("inf_model", gInfModel); prefs.end();
    }
    String upper = mode; upper.toUpperCase();
    if (upper == "LOW_POWER_SLEEP") {
      // Answer BEFORE sleeping, or the caller only ever sees a timeout.
      out["mode"] = "LOW_POWER_SLEEP";
      out["sleep_s"] = args["duration_s"] | DEFAULT_SLEEP_S;
      out["note"] = "board is entering deep sleep and will be unreachable until it wakes";
      toolResult(resp, id, out, false);
      gPendingSleepS = args["duration_s"] | DEFAULT_SLEEP_S;
      return;
    }
    String err;
    if (!applyStateMode(mode, 0, err, "")) { out["error"] = err; toolResult(resp, id, out, true); return; }
    out["mode"] = modeName(gMode);
    if (gMode == MODE_INFERENCE_ACTIVE) out["model_id"] = gInfModel;
    toolResult(resp, id, out, false);
    return;
  }

  if (name == "identify") {
    if (gUseHeartbeatLed) for (int i = 0; i < 10; i++) { digitalWrite(PIN_LED_HEARTBEAT, !digitalRead(PIN_LED_HEARTBEAT)); delay(120); }
    oledIdentify();
    out["ok"] = true; out["display"] = oledPresent();
    toolResult(resp, id, out, false);
    return;
  }

  rpcError(resp, id, -32602, "Unknown tool '" + name + "'");
}

// Shared by HTTP and MQTT. resp is empty for notifications.
void mcpDispatch(JsonObjectConst req, JsonDocument &resp, bool writeAllowed) {
  gMcpCalls++;
  JsonVariantConst id = req["id"];
  String method = req["method"] | "";
  if (req["jsonrpc"] != "2.0" || method.length() == 0) { rpcError(resp, id, -32600, "Invalid Request"); return; }
  if (id.isNull() || method.startsWith("notifications/")) { resp.clear(); return; }

  if (method == "initialize") {
    String want = req["params"]["protocolVersion"] | "";
    String ver = MCP_VERSIONS[0];
    for (const char *v : MCP_VERSIONS) if (want == v) ver = v;
    resp["jsonrpc"] = "2.0"; resp["id"] = id;
    JsonObject r = resp["result"].to<JsonObject>();
    r["protocolVersion"] = ver;
    r["capabilities"]["tools"]["listChanged"] = false;
    r["serverInfo"]["name"] = MCP_SERVER_NAME;
    r["serverInfo"]["title"] = "Ionity ESP32 node " + gDeviceId;
    r["serverInfo"]["version"] = FW_VERSION;
    r["instructions"] = "One Ionity ESP32 board. Call read_telemetry first. Write tools change real hardware.";
    return;
  }
  if (method == "ping") { resp["jsonrpc"] = "2.0"; resp["id"] = id; resp["result"].to<JsonObject>(); return; }
  if (method == "tools/list") {
    JsonDocument tools;
    deserializeJson(tools, MCP_TOOLS_JSON);
    resp["jsonrpc"] = "2.0"; resp["id"] = id;
    resp["result"]["tools"] = tools.as<JsonArray>();
    return;
  }
  if (method == "tools/call") {
    String name = req["params"]["name"] | "";
    callTool(name, req["params"]["arguments"].as<JsonObjectConst>(), id, resp, writeAllowed);
    return;
  }
  rpcError(resp, id, -32601, "Method not found: " + method);
}

// ---------------------------------------------------------------------------
// HTTP transport
// ---------------------------------------------------------------------------
uint32_t gPendingSleepS = 0;          // set_state_mode LOW_POWER_SLEEP over HTTP/MQTT

static void corsHeaders() {
  mcpHttp.sendHeader("Access-Control-Allow-Origin", "*");
  mcpHttp.sendHeader("Access-Control-Allow-Headers", "Content-Type, Authorization, Mcp-Protocol-Version, Mcp-Session-Id");
  mcpHttp.sendHeader("Access-Control-Allow-Methods", "POST, GET, OPTIONS");
}

static bool tokenEquals(const String &a, const String &b) {   // constant-time for equal lengths
  if (a.length() != b.length()) return false;
  uint8_t diff = 0;
  for (size_t i = 0; i < a.length(); i++) diff |= (uint8_t)a[i] ^ (uint8_t)b[i];
  return diff == 0;
}

static int httpAuth() {             // 0 = none, 1 = read, 2 = read+write
  if (gCfg.mcpToken.length() == 0) return 1;
  String h = mcpHttp.header("Authorization");
  return (h.startsWith("Bearer ") && tokenEquals(h.substring(7), gCfg.mcpToken)) ? 2 : 0;
}

static void handleMcpPost() {
  corsHeaders();
  int auth = httpAuth();
  if (auth == 0) { mcpHttp.send(401, "application/json", "{\"error\":\"bearer token required\"}"); return; }
  const String &body = mcpHttp.arg("plain");
  if (body.length() > MCP_HTTP_MAX_BODY) {
    mcpHttp.send(413, "application/json", "{\"jsonrpc\":\"2.0\",\"id\":null,\"error\":{\"code\":-32600,\"message\":\"request too large\"}}");
    return;
  }
  JsonDocument req;
  if (deserializeJson(req, body)) {
    mcpHttp.send(400, "application/json", "{\"jsonrpc\":\"2.0\",\"id\":null,\"error\":{\"code\":-32700,\"message\":\"Parse error\"}}");
    return;
  }
  if (req.is<JsonArray>()) {
    mcpHttp.send(400, "application/json", "{\"jsonrpc\":\"2.0\",\"id\":null,\"error\":{\"code\":-32600,\"message\":\"batches not supported\"}}");
    return;
  }
  JsonDocument resp;
  mcpDispatch(req.as<JsonObjectConst>(), resp, auth == 2);
  if (resp.isNull() || resp.size() == 0) { mcpHttp.send(202); return; }
  String out; serializeJson(resp, out);
  mcpHttp.send(200, "application/json", out);
}

void mcpHttpBegin() {
  if (gMcpHttpUp) return;
  const char *keep[] = {"Authorization"};
  mcpHttp.collectHeaders(keep, 1);
  mcpHttp.on(MCP_HTTP_PATH, HTTP_POST, handleMcpPost);
  mcpHttp.on(MCP_HTTP_PATH, HTTP_OPTIONS, []() { corsHeaders(); mcpHttp.send(204); });
  mcpHttp.on(MCP_HTTP_PATH, HTTP_GET, []() {
    corsHeaders();
    mcpHttp.send(405, "application/json", "{\"error\":\"POST JSON-RPC here; SSE streams are not offered\"}");
  });
  mcpHttp.on("/info", HTTP_GET, []() {
    corsHeaders();
    // Same rule as /mcp: open when no token is provisioned, otherwise it needs
    // the token too (it reveals site / label / host / IP).
    if (httpAuth() == 0) { mcpHttp.send(401, "application/json", "{\"error\":\"bearer token required\"}"); return; }
    JsonDocument d; fillInfo(d);
    String out; serializeJson(d, out);
    mcpHttp.send(200, "application/json", out);
  });
  mcpHttp.onNotFound([]() { corsHeaders(); mcpHttp.send(404, "application/json", "{\"error\":\"try POST /mcp or GET /info\"}"); });
  mcpHttp.begin();
  gMcpHttpUp = true;
  logln("MCP server on http://" + WiFi.localIP().toString() + MCP_HTTP_PATH +
        (gCfg.mcpToken.length() ? "  (token required)" : "  (read-only: no mcp_token)"));
}

// Called from loop(): sleep only after the reply has been sent.
void mcpAfterReply() {
  if (!gPendingSleepS) return;
  uint32_t s = gPendingSleepS; gPendingSleepS = 0;
  String err;
  delay(300);
  applyStateMode("LOW_POWER_SLEEP", s, err, "");
}
