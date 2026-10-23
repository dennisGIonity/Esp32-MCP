/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/EspPlatform.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * ESP-IDF implementations behind the portable tool interfaces:
 *   fillChipIdentity()  chip, flash, PSRAM, MAC -> hwid, app version/ELF hash, reset reason
 *   EspTelemetrySource  heap / CPU / task / die-temperature / ADC probes
 *   GpioIndicator       non-blocking identify blink on a LED GPIO
 *   EspActuatorHw       LEDC (PWM) and GPIO (relay) outputs behind ActuatorBank
 *   pinUsableForOutput  refuses strapping / flash / PSRAM / USB / console pins per chip
 * Device build only (not part of the host tests).
 */
#pragma once

#include <stdint.h>

#include <atomic>

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "tools/Actuators.h"
#include "tools/EdgeTools.h"
#include "tools/Identity.h"
#include "tools/Telemetry.h"

namespace tools {

/** Hardware/firmware part of the Identity (everything except provisioning data). */
void fillChipIdentity(Identity& id);

/** esp_reset_reason() in EWP ResetReason terms. */
uint8_t ewpResetReason();

class EspTelemetrySource final : public TelemetrySource {
public:
    struct Hooks {
        /** Live link counters (Wi-Fi RSSI/channel, publish failures, reconnects, uplink depth). */
        void (*net)(void* user, NetStats& out) = nullptr;
        /** Worst loop time of one of OUR tasks (by HELLO task id); 0 when unknown. */
        uint32_t (*runUsMax)(void* user, uint8_t taskId) = nullptr;
        /** NetObserve counters (TELEMETRY.dns); nullptr = no forwarder component. */
        void (*dns)(void* user, DnsCountersSnap& out) = nullptr;
        /** Current actuator outputs (TELEMETRY.actuators). */
        uint8_t (*actuators)(void* user, float* out, uint8_t max) = nullptr;
        void* user = nullptr;
    };

    /**
     * Installs the die temperature sensor and (adcGpio >= 0) one calibrated ADC
     * channel. Boot only. Failures disable that probe, they are not fatal.
     */
    void begin(const Identity& id, const Hooks& hooks, int adcGpio);

    /** MCP worker task only (it keeps CPU-meter state between calls). */
    void collect(TelemetrySnapshot& out, uint8_t sections) override;

private:
    void sampleCpu(CpuStats& out);
    void sampleTasks(TelemetrySnapshot& out);

    const Identity* id_ = nullptr;
    Hooks hooks_;
    void* tsens_ = nullptr;     // temperature_sensor_handle_t
    void* adc_ = nullptr;       // adc_oneshot_unit_handle_t
    void* adcCali_ = nullptr;   // adc_cali_handle_t
    int adcChannel_ = -1;

    // CPU meter: idle-task run time per core between two samples.
    bool cpuPrimed_ = false;
    int64_t cpuLastUs_ = 0;
    configRUN_TIME_COUNTER_TYPE cpuLastIdle_[2] = {0, 0};
    CpuStats cpuLast_;
};

/**
 * True when `gpio` may be driven as an output on this chip: valid output pin and
 * not a strapping, flash/PSRAM, native-USB or console-UART pin (fw 2.x rules).
 */
bool pinUsableForOutput(int gpio);

/** ActuatorBank hardware: PWM channels on LEDC (low-speed timer 0, 5 kHz, 10 bit), relays on plain GPIO. */
class EspActuatorHw final : public ActuatorHw {
public:
    bool configure(uint8_t ch, uint8_t kind, int gpio, uint8_t resBits) override;
    float write(uint8_t ch, uint8_t kind, float value) override;

private:
    int gpio_[ActuatorBank::kMaxChannels] = {-1, -1, -1};
    int ledc_[ActuatorBank::kMaxChannels] = {-1, -1, -1};
    int nextLedc_ = 0;
    bool timerReady_ = false;
};

/** identify: blink a LED from an esp_timer callback; blink() returns at once. */
class GpioIndicator final : public Indicator {
public:
    bool begin(int gpio, bool activeHigh);
    void blink(uint32_t durationMs) override;

private:
    static void onTimer(void* self);
    void set(bool on);

    esp_timer_handle_t timer_ = nullptr;
    int gpio_ = -1;
    bool activeHigh_ = true;
    bool level_ = false;                    // esp_timer task only
    std::atomic<int32_t> toggles_{0};       // remaining half-periods
    std::atomic<bool> running_{false};
};

}  // namespace tools
