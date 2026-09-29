#pragma once
// ===========================================================================
// AEDI - IONITY GLOBAL | ESP32-MCP Fleet Node - shared types
// Doc ID: DOC-2026-09-ESP32MCP-FW | Governance: Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved
// ---------------------------------------------------------------------------
// Types only. The globals themselves are defined in the main tab, because the
// Arduino builder concatenates the tabs into one file (main first) and a tab
// cannot see a global declared in a tab that comes after it.
// ===========================================================================
#include <Arduino.h>
#include "config.h"

// Everything a unit needs that differs per network / per deployment.
// Lives in NVS namespace "ionity"; written by the flasher over USB serial,
// so one image serves every WiFi and every MCP host.
struct NodeConfig {
  String   wifiSsid;
  String   wifiPass;
  String   server;       // MCP host: IP or name. Empty = mDNS ionity-fleet.local
  uint16_t mqttPort;
  uint16_t httpPort;
  String   mqttUser;
  String   mqttPass;
  String   fleetToken;
  String   mcpToken;     // bearer token for write tools on the on-device MCP
  String   otaPass;
  String   role;         // "node" (reports to the host) | "standalone" (MCP only)
};

enum StateMode : uint8_t {
  MODE_ACTIVE = 0,
  MODE_STANDBY,
  MODE_INFERENCE_ACTIVE,
  MODE_LOW_POWER_SLEEP,
  MODE_FAILSAFE,
};

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

// Fixed-size ring of floats for the edge models (no heap churn).
struct Ring {
  float    v[RING_LEN];
  uint16_t n = 0;
  uint16_t head = 0;
  void push(float x) {
    if (isnan(x)) return;
    v[head] = x;
    head = (head + 1) % RING_LEN;
    if (n < RING_LEN) n++;
  }
  // i = 0 is the oldest retained sample
  float at(uint16_t i) const { return v[(head + RING_LEN - n + i) % RING_LEN]; }
  float last() const { return n ? at(n - 1) : NAN; }
};

// Result of one edge inference.
struct Inference {
  bool   ok;
  String model;
  String label;
  float  confidence;   // 0..1
  float  score;        // model-specific raw score (z, std-dev, volts ...)
  uint16_t samples;
  uint32_t elapsed_us;
  String error;
};
