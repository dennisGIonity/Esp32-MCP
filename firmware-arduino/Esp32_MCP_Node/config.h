#pragma once
// ===========================================================================
// AEDI - IONITY GLOBAL | ESP32-MCP Fleet Node (Arduino IDE build)
// Doc ID: DOC-2026-09-ESP32MCP-FW | Governance: Policy 986 AED
// (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved
// ---------------------------------------------------------------------------
// Fleet-wide defaults. IDENTICAL on every unit.
// Per-unit values come from secrets.h and, after first boot, from NVS --
// so this one sketch flashes 1000 devices.
// ===========================================================================

#include "secrets.h"

// --- Firmware identity -----------------------------------------------------
#define FW_VERSION            "1.0.0"
#define FW_PRODUCT            "ionity-esp32-mcp-node"

// --- Device identity -------------------------------------------------------
// DEVICE_ID is derived at boot from the eFuse MAC as "esp32-aabbccddeeff".
#define DEVICE_ID_PREFIX      "esp32"
#define DEFAULT_SITE          "ionity-local"
#define DEFAULT_GROUP         "default"

// --- Central server (Ionity Local Drive host) ------------------------------
#define SERVER_HOST           "192.168.2.11"
#define SERVER_HTTP_PORT      8099
#define HTTP_INGEST_PATH      "/api/v1/telemetry"

// --- MQTT (primary transport) ----------------------------------------------
#define MQTT_HOST             SERVER_HOST
#define MQTT_PORT             1883
#define MQTT_KEEPALIVE_S      60
#define MQTT_ROOT             "ionity"
#define MQTT_CH_TELEMETRY     "telemetry"
#define MQTT_CH_STATUS        "status"
#define MQTT_CH_CMD           "cmd"
#define MQTT_CH_CMD_RESULT    "cmd/result"

// --- Transport policy ------------------------------------------------------
// MQTT primary; after this many failed broker connects the node falls back to
// HTTP POST and keeps reporting. It returns to MQTT automatically.
#define MQTT_FAIL_THRESHOLD   3
#define HTTP_FALLBACK_ENABLED 1

// --- Cadence (ms) ----------------------------------------------------------
#define TELEMETRY_INTERVAL_MS 10000
#define SENSOR_SAMPLE_MS      2000
#define HEARTBEAT_STATUS_MS   30000
#define WIFI_RETRY_MS         5000
#define MQTT_RETRY_MS         5000

// --- ICMP probing ----------------------------------------------------------
// OFF for the Arduino build: ESP32Ping is not in the Library Manager and is
// not reliable on esp32 core 3.x. latency/loss then simply aren't reported --
// the server's metrics map is open, so nothing breaks.
// Use the PlatformIO build (firmware/) if you want fleet-wide link probing.
#define PROBE_ENABLED         0

// --- Board pin map ---------------------------------------------------------
#if defined(CONFIG_IDF_TARGET_ESP32C3)
  #define PIN_LED_HEARTBEAT   8
  #define PIN_LED_ALERT       7
  #define PIN_DIGITAL_SENSE   6
  #define PIN_ANALOG_SENSE    3
#elif defined(CONFIG_IDF_TARGET_ESP32S3) || defined(CONFIG_IDF_TARGET_ESP32S2)
  #define PIN_LED_HEARTBEAT   2
  #define PIN_LED_ALERT       4
  #define PIN_DIGITAL_SENSE   18
  #define PIN_ANALOG_SENSE    3
#else   /* classic ESP32 / WROOM-32 */
  #define PIN_LED_HEARTBEAT   2
  #define PIN_LED_ALERT       4
  #define PIN_DIGITAL_SENSE   18
  #define PIN_ANALOG_SENSE    34
#endif

// --- Store-and-forward buffer ----------------------------------------------
#define OFFLINE_BUFFER_SLOTS  40

// --- OTA -------------------------------------------------------------------
#define OTA_ENABLED           1
#define OTA_HOSTNAME_PREFIX   "ionity-esp32-"

// --- Serial ----------------------------------------------------------------
#define SERIAL_BAUD           115200
#define LOG_PREFIX            "[IONITY-NODE] "
