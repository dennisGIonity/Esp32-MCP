#pragma once
// ===========================================================================
// AEDI - IONITY GLOBAL | ESP32-MCP Fleet Node Configuration
// Doc ID: DOC-2026-09-ESP32MCP-FW | Governance: Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved
// ---------------------------------------------------------------------------
// Fleet-wide defaults live here and are IDENTICAL on every unit.
// Per-unit values (WiFi creds, site, device label) come from secrets.h and,
// after first boot, from NVS -- so one binary flashes 1000 devices.
// ===========================================================================

#include "secrets.h"

// --- Firmware identity -----------------------------------------------------
#define FW_VERSION            "1.0.0"
#define FW_PRODUCT            "ionity-esp32-mcp-node"

// --- Device identity -------------------------------------------------------
// DEVICE_ID is NOT hardcoded. It is derived at boot from the eFuse MAC as
// "esp32-aabbccddeeff". Override per unit via NVS key "device_id" if needed.
#define DEVICE_ID_PREFIX      "esp32"
#define DEFAULT_SITE          "ionity-local"          // NVS key "site"
#define DEFAULT_GROUP         "default"               // NVS key "group"

// --- Central server (Ionity Local Drive host) ------------------------------
#define SERVER_HOST           "192.168.2.11"
#define SERVER_HTTP_PORT      8099                    // ESP32-MCP fleet server
#define HTTP_INGEST_PATH      "/api/v1/telemetry"
#define HTTP_REGISTER_PATH    "/api/v1/devices/register"

// --- MQTT (primary transport) ----------------------------------------------
#define MQTT_HOST             SERVER_HOST
#define MQTT_PORT             1883
#define MQTT_KEEPALIVE_S      60
// Topic plan: ionity/<site>/<device_id>/<channel>
#define MQTT_ROOT             "ionity"
#define MQTT_CH_TELEMETRY     "telemetry"
#define MQTT_CH_STATUS        "status"                // retained + LWT
#define MQTT_CH_EVENT         "event"
#define MQTT_CH_CMD           "cmd"                   // server -> device
#define MQTT_CH_CMD_RESULT    "cmd/result"            // device -> server

// --- Transport policy ------------------------------------------------------
// MQTT is primary. If the broker is unreachable for MQTT_FAIL_THRESHOLD
// consecutive attempts, the node falls back to HTTP POST until MQTT recovers.
#define MQTT_FAIL_THRESHOLD   3
#define HTTP_FALLBACK_ENABLED 1

// --- Cadence (ms) ----------------------------------------------------------
// 10s x 1000 devices = 100 msg/s. Comfortable for one Mosquitto instance.
// Raise TELEMETRY_INTERVAL_MS as the fleet grows; do not lower below 2000.
#define TELEMETRY_INTERVAL_MS 10000
#define SENSOR_SAMPLE_MS      2000
#define HEARTBEAT_STATUS_MS   30000
#define WIFI_RETRY_MS         5000
#define MQTT_RETRY_MS         5000

// --- Network probing (inherited from RouterProject sentinel) ---------------
#define PROBE_ENABLED         1
#define PROBE_INTERVAL_MS     30000
#define PROBE_TARGET_1        "8.8.8.8"
#define PROBE_TARGET_2        "1.1.1.1"
#define PROBE_GATEWAY_AUTO    1       // 4th target = this node's own gateway

// --- Local sensing pins (safe defaults; override per board) -----------------
#define PIN_LED_HEARTBEAT     2
#define PIN_LED_ALERT         4
#define PIN_DIGITAL_SENSE     5       // e.g. mains optocoupler / door contact
#define PIN_ANALOG_SENSE      34      // e.g. current clamp / LDR / battery div

// --- Store-and-forward buffer ----------------------------------------------
// Readings taken while offline are queued in RAM and flushed on reconnect.
#define OFFLINE_BUFFER_SLOTS  40

// --- OTA -------------------------------------------------------------------
#define OTA_ENABLED           1
#define OTA_HOSTNAME_PREFIX   "ionity-esp32-"

// --- Serial ----------------------------------------------------------------
#define SERIAL_BAUD           115200
#define LOG_PREFIX            "[IONITY-NODE] "
