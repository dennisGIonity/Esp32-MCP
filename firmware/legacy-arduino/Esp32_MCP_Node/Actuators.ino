// ===========================================================================
// AEDI - IONITY GLOBAL | Actuators (LEDs, PWM, relay)
// Doc ID: DOC-2026-09-ESP32MCP-ACT | Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
// ---------------------------------------------------------------------------
// set_actuator(channel, value)
//   led, alert_led  on/off (value 0 | 1)
//   pwm0, pwm1      duty 0.0..1.0 on LEDC (5 kHz, 10 bit)
//   relay0          on/off
// A pin is only claimed the first time its channel is driven, so a board
// with something else wired to GPIO 5/6/7 is left alone until asked.
// Pins are per-unit in NVS (flasher "pins" field); pins that would brick
// the board (flash/PSRAM bus, native USB, UART0) are refused.
// FAILSAFE mode forces everything to 0 and rejects writes.
// ===========================================================================

static const char *ACT_NAMES[5] = {"led", "alert_led", "pwm0", "pwm1", "relay0"};
static bool gActAttached[5] = {false, false, false, false, false};

void loadActuatorPins() {
  prefs.begin("ionity", true);
  gPinPwm0   = prefs.getInt("pin_pwm0",   PIN_PWM0_DEFAULT);
  gPinPwm1   = prefs.getInt("pin_pwm1",   PIN_PWM1_DEFAULT);
  gPinRelay0 = prefs.getInt("pin_relay0", PIN_RELAY0_DEFAULT);
  prefs.end();
}

bool pinIsSafe(int pin) {
  if (pin < 0) return false;
#if defined(CONFIG_IDF_TARGET_ESP32S3)
  if (pin >= 26 && pin <= 37) return false;          // flash / octal PSRAM
  if (pin == 19 || pin == 20) return false;           // native USB
  if (pin == 43 || pin == 44) return false;           // UART0 console
  if (pin == 0 || pin == 45 || pin == 46) return false; // strapping
  return pin <= 48;
#elif defined(CONFIG_IDF_TARGET_ESP32C3)
  if (pin >= 11 && pin <= 17) return false;           // flash
  if (pin == 18 || pin == 19) return false;           // native USB
  if (pin == 20 || pin == 21) return false;           // UART0
  if (pin == 2 || pin == 9) return false;             // strapping
  return pin <= 21;
#else
  if (pin >= 6 && pin <= 11) return false;            // flash
  if (pin == 1 || pin == 3) return false;             // UART0
  if (pin == 0 || pin == 2 || pin == 12 || pin == 15) return false; // strapping
  if (pin >= 34) return false;                        // input-only
  return pin <= 33;
#endif
}

int actIndex(const String &ch) {
  for (int i = 0; i < 5; i++) if (ch == ACT_NAMES[i]) return i;
  return -1;
}

static int actPin(int idx) {
  switch (idx) {
    case 0: return gUseHeartbeatLed ? PIN_LED_HEARTBEAT : -1;
    case 1: return gUseAlertLed ? PIN_LED_ALERT : -1;
    case 2: return gPinPwm0;
    case 3: return gPinPwm1;
    case 4: return gPinRelay0;
  }
  return -1;
}

// Returns "" on success, else the reason.
String setActuator(const String &channel, float value, bool force = false) {
  int idx = actIndex(channel);
  if (idx < 0) return "channel must be led|alert_led|pwm0|pwm1|relay0";
  if (gMode == MODE_FAILSAFE && !force && value != 0) return "FAILSAFE: actuators locked off (set_state_mode ACTIVE first)";
  if (isnan(value) || value < 0 || value > 1) return "value must be 0..1 (duty for pwm, 0/1 for on/off)";
  int pin = actPin(idx);
  if (pin < 0) return channel + " is disabled on this board (pin used by the display)";
  if (idx >= 2 && (pin == gOledSda || pin == gOledScl)) return channel + " pin " + String(pin) + " is the OLED's I2C bus on this board - move it with the flasher's pins field";
  if (idx >= 2 && !pinIsSafe(pin)) return channel + " pin " + String(pin) + " is not safe to drive on this chip";

  if (idx == 2 || idx == 3) {
    if (!gActAttached[idx]) {
      if (!ledcAttach(pin, PWM_FREQ_HZ, PWM_RES_BITS)) return "LEDC attach failed on pin " + String(pin);
      gActAttached[idx] = true;
    }
    ledcWrite(pin, (uint32_t)lroundf(value * ((1 << PWM_RES_BITS) - 1)));
  } else {
    if (!gActAttached[idx]) { pinMode(pin, OUTPUT); gActAttached[idx] = true; }
    value = value >= 0.5f ? 1 : 0;
    digitalWrite(pin, value ? HIGH : LOW);
  }
  gAct[idx] = value;
  logln("actuator " + channel + " (pin " + String(pin) + ") = " + String(value, 3));
  return "";
}

void actuatorsAllOff() {
  for (int i = 0; i < 5; i++)
    if (gActAttached[i]) setActuator(ACT_NAMES[i], 0, true);
}

void actuatorsToJson(JsonObject o) {
  for (int i = 0; i < 5; i++) {
    JsonObject a = o[ACT_NAMES[i]].to<JsonObject>();
    a["pin"] = actPin(i);
    if (gAct[i] >= 0) a["value"] = gAct[i]; else a["value"] = nullptr;
    a["claimed"] = gActAttached[i];
  }
}
