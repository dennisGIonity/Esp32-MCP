/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/EspNvs.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "prov/EspNvs.h"

#include "esp_log.h"
#include "nvs_flash.h"

namespace prov {
namespace {
const char* const TAG = "nvs";

KvStore::Get map(esp_err_t e) {
    if (e == ESP_OK) return KvStore::Get::Ok;
    if (e == ESP_ERR_NVS_NOT_FOUND) return KvStore::Get::Missing;
    return KvStore::Get::Error;  // type mismatch, too long, flash error
}
}  // namespace

esp_err_t initNvsFlash(bool* erased) {
    *erased = false;
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        // The partition is full of stale pages or was written by a NEWER NVS
        // format: the data cannot be read. Erasing is the documented recovery.
        ESP_LOGW(TAG, "NVS partition unusable (%s) - erasing; the board needs provisioning", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
        *erased = true;
    }
    return err;
}

bool NvsKv::open(bool write) {
    if (open_) return true;
    const esp_err_t e = nvs_open(ns_, write ? NVS_READWRITE : NVS_READONLY, &h_);
    open_ = e == ESP_OK;
    if (!open_ && e != ESP_ERR_NVS_NOT_FOUND) ESP_LOGE(TAG, "nvs_open(%s): %s", ns_, esp_err_to_name(e));
    return open_;
}

void NvsKv::close() {
    if (open_) nvs_close(h_);
    open_ = false;
}

KvStore::Get NvsKv::getStr(const char* key, char* out, size_t cap) {
    size_t len = cap;
    const esp_err_t e = nvs_get_str(h_, key, out, &len);
    if (e == ESP_ERR_NVS_INVALID_LENGTH) ESP_LOGW(TAG, "%s: stored value longer than %u bytes - ignored", key, static_cast<unsigned>(cap - 1));
    if (e != ESP_OK && e != ESP_ERR_NVS_NOT_FOUND && cap > 0) out[0] = '\0';
    return map(e);
}

KvStore::Get NvsKv::strLen(const char* key, size_t& len) {
    size_t n = 0;
    const esp_err_t e = nvs_get_str(h_, key, nullptr, &n);
    if (e == ESP_OK) len = n > 0 ? n - 1 : 0;  // NVS counts the NUL
    return map(e);
}

KvStore::Get NvsKv::getU8(const char* key, uint8_t& v) { return map(nvs_get_u8(h_, key, &v)); }
KvStore::Get NvsKv::getU16(const char* key, uint16_t& v) { return map(nvs_get_u16(h_, key, &v)); }
KvStore::Get NvsKv::getU32(const char* key, uint32_t& v) { return map(nvs_get_u32(h_, key, &v)); }
KvStore::Get NvsKv::getI32(const char* key, int32_t& v) { return map(nvs_get_i32(h_, key, &v)); }

bool NvsKv::setStr(const char* key, const char* v) { return nvs_set_str(h_, key, v) == ESP_OK; }
bool NvsKv::setU8(const char* key, uint8_t v) { return nvs_set_u8(h_, key, v) == ESP_OK; }
bool NvsKv::setU16(const char* key, uint16_t v) { return nvs_set_u16(h_, key, v) == ESP_OK; }
bool NvsKv::setU32(const char* key, uint32_t v) { return nvs_set_u32(h_, key, v) == ESP_OK; }
bool NvsKv::setI32(const char* key, int32_t v) { return nvs_set_i32(h_, key, v) == ESP_OK; }

bool NvsKv::erase(const char* key) {
    const esp_err_t e = nvs_erase_key(h_, key);
    return e == ESP_OK || e == ESP_ERR_NVS_NOT_FOUND;
}

bool NvsKv::eraseAll() { return nvs_erase_all(h_) == ESP_OK; }
bool NvsKv::commit() { return nvs_commit(h_) == ESP_OK; }

}  // namespace prov
