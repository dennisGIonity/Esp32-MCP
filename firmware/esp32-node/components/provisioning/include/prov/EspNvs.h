/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/EspNvs.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * KvStore on ESP-IDF NVS, namespace "ionity" (the fw 2.x Preferences
 * namespace: same keys, same types -> settings survive the upgrade).
 *
 * RTOS NOTE: NVS writes run with the flash cache disabled. Only call the
 * write paths from tasks whose stack is in internal RAM (every task this
 * firmware creates; never from a PSRAM-stack task) and never from an ISR.
 */
#pragma once

#include "nvs.h"
#include "prov/Config.h"

namespace prov {

class NvsKv final : public KvStore {
public:
    explicit NvsKv(const char* ns = "ionity") : ns_(ns) {}

    bool open(bool write) override;
    void close() override;
    Get getStr(const char* key, char* out, size_t cap) override;
    Get strLen(const char* key, size_t& len) override;
    Get getU8(const char* key, uint8_t& v) override;
    Get getU16(const char* key, uint16_t& v) override;
    Get getU32(const char* key, uint32_t& v) override;
    Get getI32(const char* key, int32_t& v) override;
    bool setStr(const char* key, const char* v) override;
    bool setU8(const char* key, uint8_t v) override;
    bool setU16(const char* key, uint16_t v) override;
    bool setU32(const char* key, uint32_t v) override;
    bool setI32(const char* key, int32_t v) override;
    bool erase(const char* key) override;
    bool eraseAll() override;
    bool commit() override;

private:
    const char* ns_;
    nvs_handle_t h_ = 0;
    bool open_ = false;
};

/** nvs_flash_init(), erasing and re-initialising only on the two documented layout errors. */
esp_err_t initNvsFlash(bool* erased);

}  // namespace prov
