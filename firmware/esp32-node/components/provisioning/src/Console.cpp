/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/Console.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "prov/Console.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#define IONITY_CONSOLE_USJ 1
#elif CONFIG_ESP_CONSOLE_UART
#include "driver/uart.h"
#include "driver/uart_vfs.h"
#define IONITY_CONSOLE_UART 1
#else
#error "esp32-node needs CONFIG_ESP_CONSOLE_UART* or CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG (see sdkconfig.defaults.*)"
#endif

namespace prov {
namespace {

const char* const TAG = "console";

#if IONITY_CONSOLE_USJ
constexpr size_t kUsjChunk = 1024;  // <= kTxBuffer: a BYTEBUF ring send is all-or-nothing
#endif

TickType_t ticks(uint32_t ms) { return ms == 0 ? 0 : (pdMS_TO_TICKS(ms) == 0 ? 1 : pdMS_TO_TICKS(ms)); }

}  // namespace

Console& Console::instance() {
    static Console c;
    return c;
}

const char* Console::transportName() const {
#if IONITY_CONSOLE_USJ
    return "USB-Serial-JTAG";
#else
    return "UART";
#endif
}

bool Console::begin() {
    if (ready_) return true;
    if (!mu_.begin(true)) return false;
#if IONITY_CONSOLE_USJ
    usb_serial_jtag_driver_config_t cfg = {};
    cfg.tx_buffer_size = kTxBuffer;
    cfg.rx_buffer_size = kRxBuffer;
    if (usb_serial_jtag_driver_install(&cfg) != ESP_OK) return false;
    usb_serial_jtag_vfs_use_driver();  // any stray printf goes through the same driver
#else
    const uart_port_t port = static_cast<uart_port_t>(CONFIG_ESP_CONSOLE_UART_NUM);
    // Baud rate stays as the bootloader/startup set it (CONFIG_ESP_CONSOLE_UART_BAUDRATE).
    if (uart_driver_install(port, kRxBuffer, kTxBuffer, 0, nullptr, 0) != ESP_OK) return false;
    uart_vfs_dev_use_driver(port);
#endif
    ready_ = true;
    esp_log_set_vprintf(&Console::logVprintf);
    return true;
}

void Console::rawWrite(const char* data, size_t len) {
#if IONITY_CONSOLE_USJ
    if (!usb_serial_jtag_is_connected()) {  // no host on the bus (power bank, charger): drop at once
        droppedWrites_.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    size_t off = 0;
    while (off < len) {
        const size_t chunk = len - off < kUsjChunk ? len - off : kUsjChunk;
        // Connected but nobody reads (port not open): wait once, then stop waiting
        // until a write succeeds again - logging must never stall the system.
        const int w = usb_serial_jtag_write_bytes(data + off, chunk, stalled_ ? 0 : ticks(50));
        if (w <= 0) {
            stalled_ = true;
            droppedWrites_.fetch_add(1, std::memory_order_relaxed);
            return;
        }
        stalled_ = false;
        off += static_cast<size_t>(w);
    }
#else
    // Blocks until the bytes are in the 4 KiB TX ring; the UART always drains (115200 baud: ~11 KiB/s).
    uart_write_bytes(static_cast<uart_port_t>(CONFIG_ESP_CONSOLE_UART_NUM), data, len);
#endif
}

void Console::write(const char* data, size_t len) {
    if (!ready_) return;
    os::Lock lk(mu_, 1000);
    if (!lk.owns()) {
        droppedWrites_.fetch_add(1, std::memory_order_relaxed);
        return;
    }
    rawWrite(data, len);
}

void Console::writeFn(void* self, const char* data, size_t len) { static_cast<Console*>(self)->write(data, len); }

void Console::flush(uint32_t timeoutMs) {
    if (!ready_) return;
#if IONITY_CONSOLE_USJ
    if (usb_serial_jtag_is_connected()) usb_serial_jtag_wait_tx_done(ticks(timeoutMs));
#else
    uart_wait_tx_done(static_cast<uart_port_t>(CONFIG_ESP_CONSOLE_UART_NUM), ticks(timeoutMs));
#endif
}

void Console::setSecrets(const NodeConfig& c) {
    os::Lock lk(mu_);
    scrub_.clear();
    scrub_.add(c.pass);
    scrub_.add(c.mqttPass);
    scrub_.add(c.fleetToken);
    scrub_.add(c.mcpToken);
    scrub_.add(c.otaPass);
}

int Console::logVprintf(const char* fmt, va_list ap) {
    Console& c = instance();
    if (!c.ready_) return vprintf(fmt, ap);
    // Logging can come from any task. Wait a little for the console, then drop:
    // a lost log line is better than a stalled Wi-Fi or MQTT task.
    if (!c.mu_.lock(100)) {
        c.droppedLogs_.fetch_add(1, std::memory_order_relaxed);
        return 0;
    }
    if (c.logDepth_ > 0) {  // re-entered from inside rawWrite (a driver logging): drop, never recurse
        c.mu_.unlock();
        c.droppedLogs_.fetch_add(1, std::memory_order_relaxed);
        return 0;
    }
    ++c.logDepth_;
    int n = vsnprintf(c.logBuf_, sizeof c.logBuf_, fmt, ap);
    size_t len = 0;
    if (n > 0) {
        len = static_cast<size_t>(n) < sizeof c.logBuf_ ? static_cast<size_t>(n) : sizeof c.logBuf_ - 1;
        if (static_cast<size_t>(n) >= sizeof c.logBuf_) {
            memcpy(c.logBuf_ + len - 4, "...\n", 4);  // truncated: still one complete line
        }
        len = c.scrub_.scrub(c.logBuf_, len);
        c.rawWrite(c.logBuf_, len);
    }
    --c.logDepth_;
    c.mu_.unlock();
    return n;
}

int Console::read(uint8_t* buf, size_t n, uint32_t timeoutMs) {
#if IONITY_CONSOLE_USJ
    return usb_serial_jtag_read_bytes(buf, n, ticks(timeoutMs));
#else
    return uart_read_bytes(static_cast<uart_port_t>(CONFIG_ESP_CONSOLE_UART_NUM), buf, n, ticks(timeoutMs));
#endif
}

bool Console::startTask(ProvHandler& handler, unsigned priority, int core, uint32_t stackBytes) {
    handler_ = &handler;
    return os::spawn("prov_console", &Console::taskEntry, this, stackBytes, priority, core, nullptr);
}

void Console::taskEntry(void* self) {
    static_cast<Console*>(self)->taskLoop();
    os::exitTask();
}

void Console::taskLoop() {
    os::wdtSubscribe();
    static uint8_t rx[256];  // console task only
    for (;;) {
        const int n = read(rx, sizeof rx, 200);
        os::wdtReset();
        if (n <= 0) continue;
        const int64_t t0 = esp_timer_get_time();
        lines_.feed(
            rx, static_cast<size_t>(n), [this](const char* line, size_t len) { handler_->onLine(line, len); },
            [this] { handler_->onOverlong(); });
        memset(rx, 0, static_cast<size_t>(n));  // raw bytes may have carried a password
        const int64_t dt = esp_timer_get_time() - t0;
        const uint32_t us = dt > 0xFFFFFFFF ? 0xFFFFFFFFu : static_cast<uint32_t>(dt);
        if (us > runUsMax_.load(std::memory_order_relaxed)) runUsMax_.store(us, std::memory_order_relaxed);
    }
}

}  // namespace prov
