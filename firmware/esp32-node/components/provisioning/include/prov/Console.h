/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/Console.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The serial console shared by the ESP-IDF log and the provisioning protocol.
 *
 *   transport   CONFIG_ESP_CONSOLE_UART*              -> UART driver (CH340 / CP210x bridge)
 *               CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG    -> USB-Serial-JTAG driver (native USB port)
 *   RX buffer   8192 bytes (>= 2 x line_max, obligation 4)
 *   atomicity   ONE mutex for every byte that leaves: log lines (esp_log_set_vprintf hook) and
 *               prov replies are written whole, so a reply is never split by a log line (5)
 *   secrets     log lines pass the Scrubber before they are written (7)
 *   no-stall    USB-Serial-JTAG: when no host is reading, writes are dropped after one 50 ms
 *               wait instead of blocking the logging task (fw 2.1.1 lesson: an 8 s stall)
 *
 * The console task reads bytes, assembles lines (LineAssembler) and hands them
 * to the ProvHandler. It is the only reader of the port.
 */
#pragma once

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

#include <atomic>

#include "mcp/os.h"
#include "prov/Config.h"
#include "prov/LineAssembler.h"
#include "prov/ProvHandler.h"
#include "prov/Scrubber.h"

namespace prov {

class Console {
public:
    static constexpr size_t kRxBuffer = 8192;
    static constexpr size_t kTxBuffer = 4096;
    static constexpr size_t kLogLineMax = 512;

    static Console& instance();

    /** Install the driver, route stdio through it and hook ESP_LOG*. Call first in app_main. */
    bool begin();

    /** One atomic write (prov replies). Not scrubbed: replies never contain secrets by construction. */
    void write(const char* data, size_t len);
    /** ProvHandler::WriteFn adapter. */
    static void writeFn(void* self, const char* data, size_t len);

    /** Wait until queued output has left (reboot: "reply, flush, wait"). */
    void flush(uint32_t timeoutMs);

    /** Refresh the secrets the log scrubber removes (after load and after every `set`). */
    void setSecrets(const NodeConfig& c);

    /** Start the reader task that feeds `handler`. */
    bool startTask(ProvHandler& handler, unsigned priority, int core, uint32_t stackBytes);

    const char* transportName() const;
    uint32_t droppedLogs() const { return droppedLogs_.load(std::memory_order_relaxed); }
    uint32_t droppedWrites() const { return droppedWrites_.load(std::memory_order_relaxed); }
    uint32_t runUsMax() const { return runUsMax_.load(std::memory_order_relaxed); }

private:
    Console() = default;
    static int logVprintf(const char* fmt, va_list ap);
    static void taskEntry(void* self);
    void taskLoop();
    int read(uint8_t* buf, size_t n, uint32_t timeoutMs);
    void rawWrite(const char* data, size_t len);  // mu_ held

    os::Mutex mu_;  // recursive: a driver may log from inside rawWrite (dropped, not deadlocked)
    bool ready_ = false;
    int logDepth_ = 0;  // guarded by mu_
    bool stalled_ = false;
    Scrubber scrub_;  // guarded by mu_
    char logBuf_[kLogLineMax] = {};
    ProvHandler* handler_ = nullptr;
    LineAssembler<ProvHandler::kLineMax> lines_;
    std::atomic<uint32_t> droppedLogs_{0};
    std::atomic<uint32_t> droppedWrites_{0};
    std::atomic<uint32_t> runUsMax_{0};
};

}  // namespace prov
