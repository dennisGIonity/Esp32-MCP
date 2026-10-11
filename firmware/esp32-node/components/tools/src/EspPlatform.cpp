/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/EspPlatform.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "tools/EspPlatform.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

#include "driver/gpio.h"
#include "driver/temperature_sensor.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_app_desc.h"
#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "freertos/task.h"
#include "sdkconfig.h"
#if CONFIG_SPIRAM
#include "esp_psram.h"
#endif

namespace tools {
namespace {

const char* const TAG = "probes";

/** First 2025-01-01: anything earlier means SNTP has not set the clock. */
constexpr time_t kClockValidAfter = 1735689600;

/** uxTaskGetSystemState() scratch. Worker-only (collect() is never re-entered). */
constexpr UBaseType_t kMaxSystemTasks = 40;
TaskStatus_t s_taskStatus[kMaxSystemTasks];

uint8_t ewpChipModel(esp_chip_model_t m) {
    switch (m) {
        case CHIP_ESP32: return EWP_CHIP_ESP32;
        case CHIP_ESP32S2: return EWP_CHIP_ESP32_S2;
        case CHIP_ESP32S3: return EWP_CHIP_ESP32_S3;
        case CHIP_ESP32C3: return EWP_CHIP_ESP32_C3;
        case CHIP_ESP32C2: return EWP_CHIP_ESP32_C2;
        case CHIP_ESP32C6: return EWP_CHIP_ESP32_C6;
        case CHIP_ESP32H2: return EWP_CHIP_ESP32_H2;
        case CHIP_ESP32P4: return EWP_CHIP_ESP32_P4;
        case CHIP_ESP32C61: return EWP_CHIP_ESP32_C61;
        case CHIP_ESP32C5: return EWP_CHIP_ESP32_C5;
        default: return EWP_CHIP_OTHER;
    }
}

}  // namespace

uint8_t ewpResetReason() {
    switch (esp_reset_reason()) {
        case ESP_RST_POWERON: return EWP_RESET_POWER_ON;
        case ESP_RST_EXT: return EWP_RESET_EXTERNAL;
        case ESP_RST_SW: return EWP_RESET_SOFTWARE;
        case ESP_RST_PANIC: return EWP_RESET_PANIC;
        case ESP_RST_INT_WDT: return EWP_RESET_INT_WDT;
        case ESP_RST_TASK_WDT: return EWP_RESET_TASK_WDT;
        case ESP_RST_WDT: return EWP_RESET_OTHER_WDT;
        case ESP_RST_DEEPSLEEP: return EWP_RESET_DEEP_SLEEP;
        case ESP_RST_BROWNOUT: return EWP_RESET_BROWNOUT;
        case ESP_RST_USB: return EWP_RESET_USB;
        case ESP_RST_JTAG: return EWP_RESET_JTAG;
        case ESP_RST_EFUSE: return EWP_RESET_EFUSE;
        case ESP_RST_PWR_GLITCH: return EWP_RESET_POWER_GLITCH;
        case ESP_RST_CPU_LOCKUP: return EWP_RESET_CPU_LOCKUP;
        default: return EWP_RESET_UNKNOWN;  // incl. ESP_RST_SDIO (no EWP code)
    }
}

void fillChipIdentity(Identity& id) {
    uint8_t mac[6] = {};
    // ESP_MAC_WIFI_STA is the base MAC: the same value fw 2.x used for device_id,
    // so a board keeps its hwid (and its retained topics) across the upgrade.
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    formatHwid(mac, id.hwid, sizeof id.hwid);

    const esp_app_desc_t* app = esp_app_get_description();
    snprintf(id.fwVersion, sizeof id.fwVersion, "%.16s", app->version);
    memcpy(id.elfSha, app->app_elf_sha256, sizeof id.elfSha);
    snprintf(id.idf, sizeof id.idf, "%.16s", esp_get_idf_version());

    esp_chip_info_t chip;
    esp_chip_info(&chip);
    id.chipModel = ewpChipModel(chip.model);
    id.chipRev = chip.revision;  // MXX: 2 = v0.2, 100 = v1.0
    id.cores = chip.cores;

    uint32_t flashBytes = 0;
    if (esp_flash_get_size(nullptr, &flashBytes) == ESP_OK) id.flashMb = static_cast<uint8_t>(flashBytes >> 20);
#if CONFIG_SPIRAM
    if (esp_psram_is_initialized()) id.psramMb = static_cast<uint8_t>(esp_psram_get_size() >> 20);
#endif
    id.resetReason = ewpResetReason();
}

/* ------------------------------------------------------------------------- */
/* Telemetry probes                                                           */
/* ------------------------------------------------------------------------- */

void EspTelemetrySource::begin(const Identity& id, const Hooks& hooks, int adcGpio) {
    id_ = &id;
    hooks_ = hooks;

    temperature_sensor_handle_t ts = nullptr;
    const temperature_sensor_config_t tcfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);
    if (temperature_sensor_install(&tcfg, &ts) == ESP_OK && temperature_sensor_enable(ts) == ESP_OK) {
        tsens_ = ts;
    } else {
        ESP_LOGW(TAG, "die temperature sensor unavailable");
    }

    if (adcGpio >= 0) {
        adc_unit_t unit;
        adc_channel_t ch;
        adc_oneshot_unit_handle_t adc = nullptr;
        if (adc_oneshot_io_to_channel(adcGpio, &unit, &ch) == ESP_OK && unit == ADC_UNIT_1) {
            // ADC1 only: ADC2 is shared with the Wi-Fi radio on these chips.
            adc_oneshot_unit_init_cfg_t ucfg = {};
            ucfg.unit_id = unit;
            adc_oneshot_chan_cfg_t ccfg = {};
            ccfg.atten = ADC_ATTEN_DB_12;
            ccfg.bitwidth = ADC_BITWIDTH_DEFAULT;
            if (adc_oneshot_new_unit(&ucfg, &adc) == ESP_OK && adc_oneshot_config_channel(adc, ch, &ccfg) == ESP_OK) {
                adc_ = adc;
                adcChannel_ = ch;
#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
                adc_cali_curve_fitting_config_t cal = {};
                cal.unit_id = unit;
                cal.chan = ch;
                cal.atten = ADC_ATTEN_DB_12;
                cal.bitwidth = ADC_BITWIDTH_DEFAULT;
                adc_cali_handle_t cali = nullptr;
                if (adc_cali_create_scheme_curve_fitting(&cal, &cali) == ESP_OK) adcCali_ = cali;
#endif
            }
        }
        if (adc_ == nullptr) ESP_LOGW(TAG, "ADC on GPIO%d unavailable (needs an ADC1 pin)", adcGpio);
    }

    // Prime the CPU meter so the first periodic sample already has a window.
    CpuStats unused;
    sampleCpu(unused);
}

void EspTelemetrySource::sampleCpu(CpuStats& out) {
    // Idle-task run time (esp_timer microseconds) per core between two samples:
    // load = 100 - idle% . Counters are unsigned: the subtraction is wrap-safe.
    const int64_t now = esp_timer_get_time();
    const uint8_t cores = (id_ != nullptr && id_->cores >= 2) ? 2 : 1;
    configRUN_TIME_COUNTER_TYPE idle[2] = {0, 0};
    for (uint8_t c = 0; c < cores; ++c) idle[c] = ulTaskGetIdleRunTimeCounterForCore(c);
    if (!cpuPrimed_) {
        cpuPrimed_ = true;
        cpuLastUs_ = now;
        cpuLastIdle_[0] = idle[0];
        cpuLastIdle_[1] = idle[1];
        out = CpuStats{};
        return;
    }
    const int64_t wall = now - cpuLastUs_;
    if (wall < 200000) {  // < 200 ms since the last sample: too noisy, repeat the last value
        out = cpuLast_;
        return;
    }
    CpuStats s;
    s.valid = true;
    s.cores = cores;
    for (uint8_t c = 0; c < cores; ++c) {
        const uint64_t idleUs = static_cast<configRUN_TIME_COUNTER_TYPE>(idle[c] - cpuLastIdle_[c]);
        const uint64_t idlePct = idleUs >= static_cast<uint64_t>(wall) ? 100u : idleUs * 100u / static_cast<uint64_t>(wall);
        s.loadPct[c] = static_cast<uint8_t>(100u - idlePct);
    }
    cpuLastUs_ = now;
    cpuLastIdle_[0] = idle[0];
    cpuLastIdle_[1] = idle[1];
    cpuLast_ = s;
    out = s;
}

void EspTelemetrySource::sampleTasks(TelemetrySnapshot& out) {
    // One scheduler-suspended walk for every task, then match OUR tasks by name.
    // Names, not handles: esp-mqtt recreates its task on a reconnect, and a
    // stale handle passed to uxTaskGetStackHighWaterMark() would be a use-after-free.
    const UBaseType_t n = uxTaskGetSystemState(s_taskStatus, kMaxSystemTasks, nullptr);
    out.taskCount = 0;
    for (uint8_t i = 0; i < id_->taskCount && out.taskCount < kMaxTaskStats; ++i) {
        const TaskInfo& t = id_->tasks[i];
        for (UBaseType_t k = 0; k < n; ++k) {
            if (strncmp(s_taskStatus[k].pcTaskName, t.name, sizeof t.name) != 0) continue;
            TaskStat& st = out.tasks[out.taskCount++];
            st.id = t.id;
            const uint32_t free = s_taskStatus[k].usStackHighWaterMark;  // bytes on ESP-IDF
            st.stackFreeMin = free > 0xFFFFu ? 0xFFFF : static_cast<uint16_t>(free);
            st.runUsMax = hooks_.runUsMax ? hooks_.runUsMax(hooks_.user, t.id) : 0;
            break;
        }
    }
}

void EspTelemetrySource::collect(TelemetrySnapshot& out, uint8_t sections) {
    out.uptimeS = static_cast<uint32_t>(esp_timer_get_time() / 1000000);
    const time_t now = time(nullptr);
    if (now > kClockValidAfter) {
        out.hasUnix = true;
        out.unixS = static_cast<uint32_t>(now);
    }
    if (sections & EWP_TM_HEAP) {
        HeapStats& h = out.heap;
        h.internalFree = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
        h.internalMinFree = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
        h.internalLargest = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL);
        h.hasDma = true;
        h.dmaFree = heap_caps_get_free_size(MALLOC_CAP_DMA);
#if CONFIG_SPIRAM
        if (esp_psram_is_initialized()) {
            h.hasPsram = true;
            h.psramFree = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
            h.psramMinFree = heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM);
            h.psramLargest = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);
        }
#endif
    }
    if ((sections & EWP_TM_NET) && hooks_.net != nullptr) hooks_.net(hooks_.user, out.net);
    if (sections & EWP_TM_CPU) sampleCpu(out.cpu);
    if (sections & EWP_TM_TASKS) sampleTasks(out);
    if (sections & EWP_TM_SENSORS) {
        float t = 0.0f;
        if (tsens_ != nullptr &&
            temperature_sensor_get_celsius(static_cast<temperature_sensor_handle_t>(tsens_), &t) == ESP_OK) {
            out.sensors.hasDieTemp = true;
            out.sensors.dieTempC = t;
        }
        int raw = 0;
        if (adc_ != nullptr &&
            adc_oneshot_read(static_cast<adc_oneshot_unit_handle_t>(adc_), static_cast<adc_channel_t>(adcChannel_), &raw) ==
                ESP_OK) {
            int mv = 0;
            if (adcCali_ != nullptr &&
                adc_cali_raw_to_voltage(static_cast<adc_cali_handle_t>(adcCali_), raw, &mv) == ESP_OK && mv >= 0) {
                out.sensors.adcMv[0] = static_cast<uint16_t>(mv > 0xFFFF ? 0xFFFF : mv);
                out.sensors.adcCount = 1;
            }
        }
    }
    // Actuators: none in this phase (set_actuator registers channels later).
    out.actuatorCount = 0;
}

/* ------------------------------------------------------------------------- */
/* identify LED                                                                */
/* ------------------------------------------------------------------------- */

bool GpioIndicator::begin(int gpio, bool activeHigh) {
    if (gpio < 0 || !GPIO_IS_VALID_OUTPUT_GPIO(gpio)) return false;
    gpio_ = gpio;
    activeHigh_ = activeHigh;
    gpio_config_t io = {};
    io.pin_bit_mask = 1ULL << gpio;
    io.mode = GPIO_MODE_OUTPUT;
    if (gpio_config(&io) != ESP_OK) return false;
    set(false);
    esp_timer_create_args_t args = {};
    args.callback = &GpioIndicator::onTimer;
    args.arg = this;
    args.dispatch_method = ESP_TIMER_TASK;
    args.name = "identify";
    return esp_timer_create(&args, &timer_) == ESP_OK;
}

void GpioIndicator::set(bool on) {
    level_ = on;
    gpio_set_level(static_cast<gpio_num_t>(gpio_), (on == activeHigh_) ? 1 : 0);
}

void GpioIndicator::blink(uint32_t durationMs) {
    if (timer_ == nullptr) return;
    // 100 ms half-period; an even count ends with the LED off.
    int32_t halves = static_cast<int32_t>(durationMs / 100);
    if (halves < 2) halves = 2;
    halves &= ~1;
    toggles_.store(halves, std::memory_order_release);
    if (!running_.exchange(true, std::memory_order_acq_rel)) esp_timer_start_once(timer_, 100 * 1000);
}

void GpioIndicator::onTimer(void* p) {
    // esp_timer task context: short and non-blocking.
    GpioIndicator* self = static_cast<GpioIndicator*>(p);
    const int32_t left = self->toggles_.fetch_sub(1, std::memory_order_acq_rel) - 1;
    if (left >= 0) self->set(!self->level_);
    if (left > 0) {
        esp_timer_start_once(self->timer_, 100 * 1000);
        return;
    }
    self->set(false);
    // Do NOT reset toggles_ here: a concurrent blink() may just have stored a new count.
    self->running_.store(false, std::memory_order_release);
    // A blink() that raced in after the last toggle restarts the sequence.
    if (self->toggles_.load(std::memory_order_acquire) > 0 && !self->running_.exchange(true, std::memory_order_acq_rel)) {
        esp_timer_start_once(self->timer_, 100 * 1000);
    }
}

}  // namespace tools
