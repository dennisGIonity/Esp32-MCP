// ===========================================================================
// AEDI - IONITY GLOBAL | On-board OLED status display (auto-detected)
// Doc ID: DOC-2026-09-ESP32MCP-FW | Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
// ---------------------------------------------------------------------------
// Why this exists: 1.0.0 never drove a display, so a board with an OLED sat
// with a blank screen. Boards wire their OLEDs to different pins, so rather
// than hard-code one board's layout, the firmware looks for it:
//
//   1. NVS "oled_sda"/"oled_scl"/"oled_drv"  (set remotely with set_display)
//   2. otherwise scan the I2C pin pairs these boards commonly use, for a
//      controller answering at 0x3C or 0x3D
//
// Pins that would break the board are never touched: native USB (19/20),
// the flash/PSRAM bus, UART0 (43/44 = the CH340 console), strap pins.
// If the display lands on a pin the node also uses as an LED or sensor, that
// LED/sensor is switched off instead - the display wins.
//
// Driver: SSD1306 by default (most 0.96" modules). 1.3" modules are usually
// SH1106 - if the screen lights up shifted/garbled, send
//   set_display {"driver":"sh1106"}
// ===========================================================================

#include <Wire.h>
#include <U8g2lib.h>

U8G2   *gOled = nullptr;
int8_t  gOledSda = -1, gOledScl = -1;
uint8_t gOledAddr = 0;
String  gOledDrv = "ssd1306";
String  gOledNote = "not searched";
unsigned long lastOled = 0;
bool    gOledInvert = false;
// gUseHeartbeatLed / gUseAlertLed / gUseDigitalSense live in the main tab:
// Arduino appends this tab after it, so main cannot see globals declared here.

bool   oledPresent() { return gOled != nullptr; }
String oledNote()    { return gOledNote; }

static bool i2cProbe(int sda, int scl, uint8_t &addrOut) {
  if (!Wire.begin(sda, scl, 100000)) return false;
  Wire.setTimeOut(10);
  bool hit = false;
  for (uint8_t a : {0x3C, 0x3D}) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { addrOut = a; hit = true; break; }
  }
  if (!hit) Wire.end();
  return hit;
}

static void heltecPower() {
  // Heltec-style boards gate the OLED supply (Vext, GPIO36, active LOW) and
  // need a reset pulse on GPIO21. GPIO36 is PSRAM on octal-PSRAM S3 modules,
  // so only touch it when no large PSRAM is fitted.
#if defined(CONFIG_IDF_TARGET_ESP32S3)
  if (ESP.getPsramSize() < 4 * 1024 * 1024) {
    pinMode(36, OUTPUT); digitalWrite(36, LOW);
  }
  pinMode(21, OUTPUT); digitalWrite(21, LOW); delay(20); digitalWrite(21, HIGH); delay(20);
#endif
}

void oledDetect() {
  prefs.begin("ionity", true);
  gOledDrv = prefs.getString("oled_drv", "ssd1306");
  int nvSda = prefs.getInt("oled_sda", -1);
  int nvScl = prefs.getInt("oled_scl", -1);
  prefs.end();

  if (gOledDrv == "off") { gOledNote = "disabled (oled_drv=off)"; logln("OLED: disabled by NVS"); return; }

  uint8_t addr = 0;
  if (nvSda >= 0 && nvScl >= 0) {
    if (i2cProbe(nvSda, nvScl, addr)) { gOledSda = nvSda; gOledScl = nvScl; gOledAddr = addr; }
    else { gOledNote = "pinned pins " + String(nvSda) + "/" + String(nvScl) + " - no answer"; }
  } else {
#if defined(CONFIG_IDF_TARGET_ESP32S3)
    // {SDA, SCL}. Order = most likely first. 5/6 is the RouterProject sentinel.
    const int8_t pairs[][2] = {{5,6},{17,18},{8,9},{41,42},{1,2},{47,48},{4,5},
                               {39,40},{15,16},{11,12},{13,14},{21,47},{38,39},{2,1},{6,5},{18,17},{9,8}};
#elif defined(CONFIG_IDF_TARGET_ESP32C3)
    const int8_t pairs[][2] = {{5,6},{8,9},{4,5},{2,3},{6,7}};
#else
    const int8_t pairs[][2] = {{21,22},{4,15},{5,4},{22,21},{16,17},{25,26},{13,14}};
#endif
    for (auto &p : pairs) {
      if (p[0] == 17 && p[1] == 18) heltecPower();
      if (i2cProbe(p[0], p[1], addr)) { gOledSda = p[0]; gOledScl = p[1]; gOledAddr = addr; break; }
    }
    if (gOledSda < 0) {
      // Fallback: every safe GPIO pair (~1-2 s). Found pins are saved to NVS
      // so the next boot goes straight to them.
#if defined(CONFIG_IDF_TARGET_ESP32S3)
      const int8_t pool[] = {1,2,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,21,38,39,40,41,42,45,46,47,48};
#elif defined(CONFIG_IDF_TARGET_ESP32C3)
      const int8_t pool[] = {1,2,3,4,5,6,7,8,10};
#else
      const int8_t pool[] = {4,5,13,14,15,16,17,18,19,21,22,23,25,26,27,32,33};
#endif
      logln("OLED: not on common pins - scanning all safe GPIO pairs...");
      for (int8_t sda : pool) {
        for (int8_t scl : pool) {
          if (sda == scl) continue;
          if (i2cProbe(sda, scl, addr)) { gOledSda = sda; gOledScl = scl; gOledAddr = addr; break; }
        }
        if (gOledSda >= 0) break;
      }
      if (gOledSda >= 0) {
        prefs.begin("ionity", false);
        prefs.putInt("oled_sda", gOledSda); prefs.putInt("oled_scl", gOledScl);
        prefs.end();
      } else {
        char n[96];
        snprintf(n, sizeof(n), "no I2C display on any GPIO pair (psram %uMB) - SPI/parallel screen?",
                 (unsigned)(ESP.getPsramSize() >> 20));
        gOledNote = n;
      }
    }
  }

  if (gOledSda < 0) { logln("OLED: " + gOledNote); return; }

  // Display wins any pin clash with the node's LEDs / sensor.
  auto clash = [](int p) { return p == gOledSda || p == gOledScl; };
  if (clash(PIN_LED_HEARTBEAT)) gUseHeartbeatLed = false;
  if (clash(PIN_LED_ALERT))     gUseAlertLed = false;
  if (clash(PIN_DIGITAL_SENSE)) gUseDigitalSense = false;

  if (gOledDrv == "sh1106") gOled = new U8G2_SH1106_128X64_NONAME_F_HW_I2C(U8G2_R0, U8X8_PIN_NONE, gOledScl, gOledSda);
  else                      gOled = new U8G2_SSD1306_128X64_NONAME_F_HW_I2C(U8G2_R0, U8X8_PIN_NONE, gOledScl, gOledSda);
  gOled->setI2CAddress(gOledAddr << 1);
  gOled->begin();
  gOled->setContrast(200);

  char buf[80];
  snprintf(buf, sizeof(buf), "%s @0x%02X on SDA %d / SCL %d", gOledDrv.c_str(), gOledAddr, gOledSda, gOledScl);
  gOledNote = buf;
  logln("OLED: found " + gOledNote);
  oledSplash();
}

void oledSplash() {
  if (!gOled) return;
  gOled->clearBuffer();
  gOled->setFont(u8g2_font_7x14B_tf);
  gOled->drawStr(0, 14, "IONITY LAB");
  gOled->setFont(u8g2_font_5x8_tf);
  gOled->drawStr(0, 28, gDeviceId.c_str());
  gOled->drawStr(0, 40, "fw " FW_VERSION "  booting...");
  gOled->drawStr(0, 62, "Building Tomorrow, Today.");
  gOled->sendBuffer();
}

void oledUpdate() {
  if (!gOled || millis() - lastOled < 2000) return;
  lastOled = millis();
  char l[40];
  gOled->clearBuffer();

  gOled->setFont(u8g2_font_6x12_tf);
  String name = gLabel.length() ? gLabel : gDeviceId;
  if (name.length() > 21) name = name.substring(0, 21);
  gOled->drawStr(0, 10, name.c_str());
  gOled->drawHLine(0, 12, 128);

  gOled->setFont(u8g2_font_5x8_tf);
  bool up = (WiFi.status() == WL_CONNECTED);
  snprintf(l, sizeof(l), "%s %s", up ? WiFi.localIP().toString().c_str() : "no wifi",
           gUseHttp ? "HTTP" : (mqtt.connected() ? "MQTT" : "--"));
  gOled->drawStr(0, 22, l);
  snprintf(l, sizeof(l), "srv %s (%s)", gServerHost.c_str(), gServerVia.c_str());
  gOled->drawStr(0, 31, l);

  gOled->setFont(u8g2_font_7x14B_tf);
  if (!isnan(gLatest.temp_c)) { snprintf(l, sizeof(l), "%.1fC", gLatest.temp_c); gOled->drawStr(0, 47, l); }
  if (!isnan(gLatest.rssi_dbm)) { snprintf(l, sizeof(l), "%ddBm", (int)gLatest.rssi_dbm); gOled->drawStr(64, 47, l); }

  gOled->setFont(u8g2_font_5x8_tf);
  unsigned long s = millis() / 1000;
  snprintf(l, sizeof(l), "tx %lu/%lu  up %luh%02lum", (unsigned long)gTxOk, (unsigned long)gTxFail,
           s / 3600, (s / 60) % 60);
  gOled->drawStr(0, 62, l);
  gOled->sendBuffer();
}

// "identify" flashes the screen as well as (or instead of) the LED.
void oledIdentify() {
  if (!gOled) return;
  for (int i = 0; i < 6; i++) { gOledInvert = !gOledInvert; gOled->sendF("c", gOledInvert ? 0xA7 : 0xA6); delay(150); }
  gOled->sendF("c", 0xA6);
}
