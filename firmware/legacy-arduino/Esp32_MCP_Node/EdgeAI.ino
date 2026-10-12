// ===========================================================================
// AEDI - IONITY GLOBAL | Edge inference + device state modes
// Doc ID: DOC-2026-09-ESP32MCP-EDGE | Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
// ---------------------------------------------------------------------------
// run_inference(model_id, input_frame) - three models that run on every
// ESP32 with no extra libraries or model files:
//
//   anomaly_zscore   z-score of the newest sample against the rolling window
//                    (analog input by default). |z| > 3 -> "anomaly".
//   rssi_motion      std-dev of WiFi RSSI over ~16 s. People moving between
//                    the board and the AP make RSSI jitter; > 2.5 dB -> "motion".
//                    A coarse presence signal, not CSI - honest about that.
//   analog_threshold newest analog sample vs a threshold (default 1.65 V).
//
// input_frame (array of numbers) replaces the live window, so an agent can
// test a model on data it supplies. A TFLite Micro / ESP-DL model plugs in
// as another case in runInference() with the same Inference result shape.
//
// State modes:
//   ACTIVE            normal (telemetry every 10 s)
//   STANDBY           telemetry every 60 s, slower loop, no inference
//   INFERENCE_ACTIVE  runs the selected model every 2 s, adds inf_* metrics
//   LOW_POWER_SLEEP   deep sleep for duration_s, then boots back to ACTIVE
//   FAILSAFE          every actuator off and locked; persists across reboot
// ===========================================================================

const char *modeName(StateMode m) {
  switch (m) {
    case MODE_STANDBY:          return "STANDBY";
    case MODE_INFERENCE_ACTIVE: return "INFERENCE_ACTIVE";
    case MODE_LOW_POWER_SLEEP:  return "LOW_POWER_SLEEP";
    case MODE_FAILSAFE:         return "FAILSAFE";
    default:                    return "ACTIVE";
  }
}

bool parseMode(const String &s, StateMode &out) {
  String u = s; u.toUpperCase();
  if (u == "ACTIVE")           { out = MODE_ACTIVE; return true; }
  if (u == "STANDBY")          { out = MODE_STANDBY; return true; }
  if (u == "INFERENCE_ACTIVE") { out = MODE_INFERENCE_ACTIVE; return true; }
  if (u == "LOW_POWER_SLEEP")  { out = MODE_LOW_POWER_SLEEP; return true; }
  if (u == "FAILSAFE")         { out = MODE_FAILSAFE; return true; }
  return false;
}

void loadStateMode() {
  prefs.begin("ionity", true);
  uint8_t m = prefs.getUChar("mode", MODE_ACTIVE);
  gInfModel = prefs.getString("inf_model", "anomaly_zscore");
  prefs.end();
  if (m == MODE_LOW_POWER_SLEEP || m > MODE_FAILSAFE) m = MODE_ACTIVE;   // woke up
  gMode = (StateMode)m;
  if (gMode == MODE_FAILSAFE) logln("FAILSAFE mode is latched - actuators locked off");
}

// Returns false (with err) if refused. cmdId only used for logging.
bool applyStateMode(const String &modeStr, uint32_t durationS, String &err, const String &cmdId) {
  StateMode next;
  if (!parseMode(modeStr, next)) { err = "mode must be STANDBY|ACTIVE|INFERENCE_ACTIVE|LOW_POWER_SLEEP|FAILSAFE"; return false; }
  StateMode prev = gMode;
  gMode = next;
  logln(String("state mode ") + modeName(prev) + " -> " + modeName(next));

  if (next == MODE_FAILSAFE) actuatorsAllOff();
  if (next != MODE_LOW_POWER_SLEEP) {
    prefs.begin("ionity", false); prefs.putUChar("mode", (uint8_t)next); prefs.end();
  }
  if (next == MODE_LOW_POWER_SLEEP) {
    if (durationS < 5) durationS = 5;
    if (durationS > 86400) durationS = 86400;
    // Tell the host why we are going quiet, then sleep. The Last Will would
    // otherwise report a crash.
    if (mqtt.connected()) {
      JsonDocument d;
      d["device_id"] = gDeviceId; d["site"] = gSite; d["group"] = gGroup;
      d["label"] = gLabel; d["fw"] = FW_VERSION; d["state"] = "sleeping";
      d["sleep_s"] = durationS; d["mode"] = "LOW_POWER_SLEEP";
      String out; serializeJson(d, out);
      mqtt.publish(tStatus.c_str(), out.c_str(), true);
      mqtt.loop(); delay(150);
      mqtt.disconnect();
    }
    actuatorsAllOff();
    logln("deep sleep for " + String(durationS) + " s");
    Serial.flush();
    esp_sleep_enable_timer_wakeup((uint64_t)durationS * 1000000ULL);
    esp_deep_sleep_start();          // never returns
  }
  return true;
}

static void statsOf(const float *x, uint16_t n, float &mean, float &sd) {
  double s = 0, s2 = 0;
  for (uint16_t i = 0; i < n; i++) { s += x[i]; s2 += (double)x[i] * x[i]; }
  mean = n ? s / n : NAN;
  double var = n > 1 ? (s2 - s * s / n) / (n - 1) : 0;
  sd = var > 0 ? sqrt(var) : 0;
}

// frame / frameLen: optional caller-supplied data (nullptr = live ring).
Inference runInference(const String &model, const float *frame, uint16_t frameLen, float threshold) {
  Inference r;
  r.ok = false; r.model = model; r.confidence = 0; r.score = NAN; r.samples = 0;
  uint32_t t0 = micros();
  static float buf[RING_LEN];

  auto fromRing = [&](const Ring &ring) -> uint16_t {
    for (uint16_t i = 0; i < ring.n; i++) buf[i] = ring.at(i);
    return ring.n;
  };
  uint16_t n = 0;
  const float *x = buf;
  if (frame && frameLen) { x = frame; n = frameLen; }

  if (model == "anomaly_zscore") {
    if (!frame) n = fromRing(gRingAnalog);
    if (n < 8) { r.error = "need >= 8 samples (window fills in ~2 s)"; r.samples = n; return r; }
    float mean, sd;
    statsOf(x, n - 1, mean, sd);                   // baseline excludes the newest point
    float z = sd > 1e-6f ? (x[n - 1] - mean) / sd : 0;
    float lim = isnan(threshold) ? 3.0f : threshold;
    r.score = z;
    r.label = fabsf(z) > lim ? "anomaly" : "normal";
    r.confidence = fminf(1.0f, fabsf(z) / (lim * 1.333f));
    if (r.label == "normal") r.confidence = 1.0f - r.confidence;
  } else if (model == "rssi_motion") {
    if (!frame) n = fromRing(gRingRssi);
    if (n < 8) { r.error = "need >= 8 RSSI samples (WiFi must be up)"; r.samples = n; return r; }
    float mean, sd;
    statsOf(x, n, mean, sd);
    float lim = isnan(threshold) ? 2.5f : threshold;
    r.score = sd;
    r.label = sd > lim ? "motion" : "still";
    float c = fminf(1.0f, sd / (lim * 2));
    r.confidence = r.label == "motion" ? fmaxf(0.5f, c) : 1.0f - c;
  } else if (model == "analog_threshold") {
    if (!frame) n = fromRing(gRingAnalog);
    if (n < 1) { r.error = "no samples yet"; return r; }
    float lim = isnan(threshold) ? 1.65f : threshold;
    r.score = x[n - 1];
    r.label = r.score > lim ? "above" : "below";
    r.confidence = fminf(1.0f, 0.5f + fabsf(r.score - lim) / 3.3f);
  } else {
    r.error = "unknown model_id (anomaly_zscore | rssi_motion | analog_threshold)";
    return r;
  }
  r.ok = true;
  r.samples = n;
  r.elapsed_us = micros() - t0;
  gInferenceRuns++;
  return r;
}

// INFERENCE_ACTIVE: one run per sensor sample; positives go to the host as events.
void edgeTick() {
  Inference r = runInference(gInfModel, nullptr, 0, NAN);
  if (!r.ok) return;
  bool wasPositive = gLastInference.ok && (gLastInference.label == "anomaly" || gLastInference.label == "motion" || gLastInference.label == "above");
  gLastInference = r;
  bool positive = r.label == "anomaly" || r.label == "motion" || r.label == "above";
  if (positive && !wasPositive && mqtt.connected()) {
    JsonDocument d;
    d["device_id"] = gDeviceId; d["cmd_id"] = "edge";
    d["ok"] = true;
    JsonDocument e;
    e["event"] = "inference"; e["model"] = r.model; e["label"] = r.label;
    e["confidence"] = r.confidence; e["score"] = r.score;
    String detail; serializeJson(e, detail);
    d["detail"] = detail;
    String out; serializeJson(d, out);
    mqtt.publish(tCmdResult.c_str(), out.c_str(), false);
    logln("edge event: " + r.model + " -> " + r.label);
  }
}
