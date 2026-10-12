// ===========================================================================
// AEDI - IONITY GLOBAL | WiFi rescue: known networks + remote WiFi rotation
// Doc ID: DOC-2026-10-ESP32MCP-WIFI | Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
// ---------------------------------------------------------------------------
// Why (2026-09-29 / 10-03): the lab router was factory-reset and its 2.4 GHz
// network came back under a new name. Every board kept asking for the old
// SSID (reason 201) until someone walked over with a cable. Two fixes:
//
//  1. KNOWN NETWORKS. Every network a board has actually joined is remembered
//     (up to 3, NVS kn0..kn2). When the configured SSID has not been seen for
//     WIFI_RESCUE_AFTER consecutive "SSID not found" results, the board scans
//     and joins any known network it can see - e.g. the household WiFi it was
//     provisioned on before being moved to the lab. The host then sees it
//     again (via mDNS or the pinned host address) and it can be re-pointed.
//
//  2. set_wifi COMMAND (host -> board over MQTT, admin-gated at the host).
//     Rotate the whole fleet to a new SSID/password BEFORE the router changes:
//        send_command set_wifi {"ssid":"New-Net","pass":"..."}  (device or broadcast)
//     The board stores the new network as primary, keeps the current one as a
//     known fallback, replies, then reconnects. Nothing is re-flashed.
// ===========================================================================

#define KNOWN_MAX 3

struct KnownNet { String ssid, pass; };
static KnownNet gKnown[KNOWN_MAX];
static uint8_t  gNotFound = 0;          // consecutive reason-201 results

void loadKnownNetworks() {
  prefs.begin("ionity", true);
  for (int i = 0; i < KNOWN_MAX; i++) {
    gKnown[i].ssid = prefs.getString((String("kn") + i + "_ssid").c_str(), "");
    gKnown[i].pass = prefs.getString((String("kn") + i + "_pass").c_str(), "");
  }
  prefs.end();
}

static void saveKnownNetworks() {
  prefs.begin("ionity", false);
  for (int i = 0; i < KNOWN_MAX; i++) {
    prefs.putString((String("kn") + i + "_ssid").c_str(), gKnown[i].ssid);
    prefs.putString((String("kn") + i + "_pass").c_str(), gKnown[i].pass);
  }
  prefs.end();
}

// Move (ssid, pass) to the front of the known list (dedupe by ssid).
void rememberNetwork(const String &ssid, const String &pass) {
  if (ssid.length() == 0) return;
  int at = KNOWN_MAX - 1;
  for (int i = 0; i < KNOWN_MAX; i++) if (gKnown[i].ssid == ssid) { at = i; break; }
  for (int i = at; i > 0; i--) gKnown[i] = gKnown[i - 1];
  gKnown[0] = {ssid, pass};
  saveKnownNetworks();
}

int knownNetworkCount() {
  int n = 0;
  for (auto &k : gKnown) if (k.ssid.length()) n++;
  return n;
}

void knownNetworksToJson(JsonArray arr) {        // names only, never passwords
  for (auto &k : gKnown) if (k.ssid.length()) arr.add(k.ssid);
}

// Called by ensureWifi() after each failed attempt. Returns true when it
// switched the active network (caller then retries immediately).
bool wifiRescueTick(uint8_t reason) {
  if (reason != 201) { gNotFound = 0; return false; }
  if (++gNotFound < WIFI_RESCUE_AFTER) return false;
  gNotFound = 0;
  if (knownNetworkCount() == 0) return false;

  logln("WiFi rescue: \"" + gCfg.wifiSsid + "\" not seen " + String(WIFI_RESCUE_AFTER) +
        " times - scanning for a known network");
  WiFi.disconnect(false, false);
  delay(100);
  int n = WiFi.scanNetworks(false, true);
  if (n <= 0) { WiFi.scanDelete(); return false; }
  int bestIdx = -1, bestRssi = -127;
  for (int i = 0; i < n; i++) {
    String s = WiFi.SSID(i);
    if (s == gCfg.wifiSsid) { WiFi.scanDelete(); return false; }   // it's back - normal retry
    for (int k = 0; k < KNOWN_MAX; k++)
      if (gKnown[k].ssid.length() && gKnown[k].ssid == s && WiFi.RSSI(i) > bestRssi) { bestRssi = WiFi.RSSI(i); bestIdx = k; }
  }
  WiFi.scanDelete();
  if (bestIdx < 0) { logln("WiFi rescue: no known network in range"); return false; }

  logln("WiFi rescue: joining known network \"" + gKnown[bestIdx].ssid + "\" (" + String(bestRssi) + " dBm)");
  gCfg.wifiSsid = gKnown[bestIdx].ssid;
  gCfg.wifiPass = gKnown[bestIdx].pass;
  prefs.begin("ionity", false);
  prefs.putString("wifi_ssid", gCfg.wifiSsid);
  prefs.putString("wifi_pass", gCfg.wifiPass);
  prefs.end();
  gWifiReason = 0;
  return true;
}

// set_wifi {"ssid":..,"pass":..}: new primary, current kept as fallback.
String applySetWifi(JsonVariantConst in) {
  String ssid = in["ssid"] | "";
  String pass = in["pass"] | "";
  if (ssid.length() == 0 || ssid.length() > 32) return "ssid must be 1-32 chars";
  if (pass.length() > 0 && (pass.length() < 8 || pass.length() > 63)) return "WPA2 password must be 8-63 chars (or empty)";
  if (gCfg.wifiSsid.length()) rememberNetwork(gCfg.wifiSsid, gCfg.wifiPass);
  gCfg.wifiSsid = ssid; gCfg.wifiPass = pass;
  prefs.begin("ionity", false);
  prefs.putString("wifi_ssid", ssid);
  prefs.putString("wifi_pass", pass);
  prefs.end();
  return "";
}
