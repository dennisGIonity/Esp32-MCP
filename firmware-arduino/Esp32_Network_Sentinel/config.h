#pragma once
#include "secrets.h"

// ============================================================================
// Ionity_ESP_Reporter: ESP32-S3 Network Sentinel Configuration
// Site: Kelvin Drive HQ (Dynamic@KelvinDrive)
// ============================================================================

// Device Identity
#define SENTINEL_DEVICE_ID      "ESP32-S3-IONITY-01"
#define SENTINEL_FIRMWARE_VER   "v2.5.0-oled"
#define SITE_NAME               "Kelvin Drive HQ"

// Hardware Pin Definitions (ESP32-S3)
#define PIN_MAINS_SENSE         4    // 230V AC Optocoupler Input (HIGH = Power ON)
#define PIN_I2C_SDA             5    // OLED SDA (GPIO 5)
#define PIN_I2C_SCL             6    // OLED SCL (GPIO 6)
#define PIN_LED_HEARTBEAT       7    // Blue Heartbeat LED (Optional)
#define PIN_LED_ALERT           8    // Red Fault / Outage LED (Optional)
#define PIN_RELAY_FAILOVER      9    // Optional Failover Trigger Relay

// Display Type: 0 = SSD1306 0.96", 1 = SH1106 1.3"
#define DISPLAY_TYPE_SH1106     0    // Default 0 for standard SSD1306 128x64

// Time & Clock Settings (South Africa Standard Time: UTC + 2)
#define NTP_SERVER_1            "pool.ntp.org"
#define NTP_SERVER_2            "time.google.com"
#define GMT_OFFSET_SEC          7200 // UTC+2 = 2 * 3600
#define DAYLIGHT_OFFSET_SEC     0

// SPI Ethernet (W5500) Pins (Optional if using SPI Ethernet module)
#define PIN_ETH_CS              10
#define PIN_ETH_MOSI            11
#define PIN_ETH_MISO            12
#define PIN_ETH_SCK             13
#define PIN_ETH_INT             14

// WiFi credentials live in secrets.h (git-ignored) - copy secrets.h.example

// Static Network Config (Set true if on fixed VLAN 200, false for DHCP)
#define USE_STATIC_IP           false
#define STATIC_IP_ADDR          10, 53, 20, 155
#define STATIC_GATEWAY          10, 53, 20, 1
#define STATIC_SUBNET           255, 255, 255, 0
#define STATIC_PRIMARY_DNS      8, 8, 8, 8
#define STATIC_SECONDARY_DNS    1, 1, 1, 1

// Central Sentinel Telemetry Server (Your local PC IP)
#define SENTINEL_SERVER_HOST    "192.168.0.5"
#define SENTINEL_SERVER_PORT    8000
#define SENTINEL_API_ENDPOINT   "/api/telemetry/hardware-feed"

// Ping Targets for Link Stability Analysis
#define TARGET_WAN1_GW          "102.33.102.217"
#define TARGET_WAN2_GW          "41.169.150.193"
#define TARGET_PUBLIC_DNS1      "8.8.8.8"
#define TARGET_PUBLIC_DNS2      "1.1.1.1"

// Intervals (Milliseconds)
#define DISPLAY_UPDATE_INTERVAL_MS 250   // Smooth clock display (4 FPS)
#define TELEMETRY_INTERVAL_MS      3000  // Telemetry push every 3s
#define PING_PROBE_INTERVAL_MS     2000  // Probe interval 2s
#define MAINS_CHECK_INTERVAL_MS    500   // Fast power sense 500ms
