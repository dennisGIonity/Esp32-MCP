/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | mcp/os.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "mcp/os.h"

#include <stdlib.h>
#include <string.h>

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#else
#include <chrono>
#include <random>
#include <thread>
#endif

namespace os {

#ifdef ESP_PLATFORM

namespace {
const char* const TAG = "os";

/** ms -> ticks, rounded UP (1 ms must not become 0 ticks at 100 Hz) and overflow-safe. */
TickType_t toTicks(uint32_t ms) {
    if (ms == kForever) return portMAX_DELAY;
    const uint64_t t = (static_cast<uint64_t>(ms) * configTICK_RATE_HZ + 999u) / 1000u;
    return t >= portMAX_DELAY ? portMAX_DELAY - 1 : static_cast<TickType_t>(t);
}
}  // namespace

int64_t nowUs() { return esp_timer_get_time(); }
uint32_t random32() { return esp_random(); }
void delayMs(uint32_t ms) { vTaskDelay(toTicks(ms)); }

void* allocPool(size_t bytes, bool preferPsram) {
    void* p = nullptr;
    if (preferPsram) p = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p == nullptr) p = heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (p != nullptr) {
        // Zeroed so a bug that sends an unwritten slot leaks zeros, not stale frames.
        memset(p, 0, bytes);
    } else {
        ESP_LOGE(TAG, "pool allocation of %u bytes failed", static_cast<unsigned>(bytes));
    }
    return p;
}

void freePool(void* p) { heap_caps_free(p); }

uint32_t internalFreeBytes() { return static_cast<uint32_t>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)); }

void wdtSubscribe() {
    // ESP_ERR_INVALID_STATE: TWDT not initialised (CONFIG_ESP_TASK_WDT_INIT=n) - then there is nothing to feed.
    const esp_err_t err = esp_task_wdt_add(nullptr);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) ESP_LOGW(TAG, "TWDT subscribe failed: %d", err);
}
void wdtReset() { (void)esp_task_wdt_reset(); }
void wdtUnsubscribe() { (void)esp_task_wdt_delete(nullptr); }

bool spawn(const char* name, TaskFn fn, void* arg, uint32_t stackBytes, unsigned priority, int core,
           void** handle) {
    TaskHandle_t h = nullptr;
    // ESP-IDF stack depth is in BYTES (not words like upstream FreeRTOS).
    // Stacks come from internal RAM: tasks that write NVS/flash must never run
    // on a PSRAM stack (the cache is off during flash writes).
    const BaseType_t affinity = core < 0 ? tskNO_AFFINITY : static_cast<BaseType_t>(core);
    const BaseType_t ok = xTaskCreatePinnedToCore(fn, name, stackBytes, arg, priority, &h, affinity);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "task %s: not enough RAM for a %u-byte stack", name, static_cast<unsigned>(stackBytes));
        return false;
    }
    if (handle) *handle = h;
    return true;
}

void exitTask() { vTaskDelete(nullptr); }

Mutex::~Mutex() {
    if (m_) vSemaphoreDelete(m_);
}

bool Mutex::begin(bool recursive) {
    if (m_) return false;
    recursive_ = recursive;
    m_ = recursive ? xSemaphoreCreateRecursiveMutex() : xSemaphoreCreateMutex();
    return m_ != nullptr;
}

bool Mutex::lock(uint32_t timeoutMs) {
    if (m_ == nullptr) return false;
    return (recursive_ ? xSemaphoreTakeRecursive(m_, toTicks(timeoutMs)) : xSemaphoreTake(m_, toTicks(timeoutMs))) == pdTRUE;
}

void Mutex::unlock() {
    if (m_ == nullptr) return;
    if (recursive_) {
        xSemaphoreGiveRecursive(m_);
    } else {
        xSemaphoreGive(m_);
    }
}

Queue::~Queue() {
    if (q_) vQueueDelete(q_);
}

bool Queue::begin(size_t itemSize, size_t capacity) {
    if (q_ || itemSize == 0 || capacity == 0) return false;
    q_ = xQueueCreate(capacity, itemSize);
    itemSize_ = itemSize;
    capacity_ = capacity;
    return q_ != nullptr;
}

bool Queue::send(const void* item, uint32_t timeoutMs) { return xQueueSendToBack(q_, item, toTicks(timeoutMs)) == pdTRUE; }
bool Queue::sendToFront(const void* item, uint32_t timeoutMs) {
    return xQueueSendToFront(q_, item, toTicks(timeoutMs)) == pdTRUE;
}
bool Queue::receive(void* item, uint32_t timeoutMs) { return xQueueReceive(q_, item, toTicks(timeoutMs)) == pdTRUE; }
size_t Queue::waiting() const { return q_ ? uxQueueMessagesWaiting(q_) : 0; }
bool Queue::valid() const { return q_ != nullptr; }

#else  // ---------------------------------------------------------------- host

namespace {
std::chrono::steady_clock::time_point bootTime() {
    static const auto t0 = std::chrono::steady_clock::now();
    return t0;
}
}  // namespace

int64_t nowUs() {
    return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - bootTime()).count();
}

uint32_t random32() {
    static std::mutex m;
    static std::mt19937 rng(12345u);  // deterministic: tests must be reproducible
    std::lock_guard<std::mutex> lk(m);
    return static_cast<uint32_t>(rng());
}

void delayMs(uint32_t ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }

void* allocPool(size_t bytes, bool) { return calloc(1, bytes); }
void freePool(void* p) { free(p); }
uint32_t internalFreeBytes() { return 0; }
void wdtSubscribe() {}
void wdtReset() {}
void wdtUnsubscribe() {}

bool spawn(const char*, TaskFn fn, void* arg, uint32_t, unsigned, int, void** handle) {
    std::thread t(fn, arg);
    t.detach();
    if (handle) *handle = nullptr;
    return true;
}

void exitTask() {}

Mutex::~Mutex() = default;

bool Mutex::begin(bool recursive) {
    recursive_ = recursive;
    return true;
}

bool Mutex::lock(uint32_t timeoutMs) {
    // One recursive_timed_mutex models both kinds on the host; tests never re-lock a plain mutex.
    if (timeoutMs == kForever) {
        m_.lock();
        return true;
    }
    return m_.try_lock_for(std::chrono::milliseconds(timeoutMs));
}

void Mutex::unlock() { m_.unlock(); }

Queue::~Queue() { free(buf_); }

bool Queue::begin(size_t itemSize, size_t capacity) {
    if (buf_ || itemSize == 0 || capacity == 0) return false;
    buf_ = static_cast<uint8_t*>(calloc(capacity, itemSize));
    itemSize_ = itemSize;
    capacity_ = capacity;
    return buf_ != nullptr;
}

bool Queue::put(const void* item, uint32_t timeoutMs, bool front) {
    std::unique_lock<std::mutex> lk(m_);
    auto hasRoom = [this] { return count_ < capacity_; };
    if (timeoutMs == kForever) {
        notFull_.wait(lk, hasRoom);
    } else if (!notFull_.wait_for(lk, std::chrono::milliseconds(timeoutMs), hasRoom)) {
        return false;
    }
    size_t idx;
    if (front) {
        head_ = (head_ + capacity_ - 1) % capacity_;
        idx = head_;
    } else {
        idx = (head_ + count_) % capacity_;
    }
    memcpy(buf_ + idx * itemSize_, item, itemSize_);
    ++count_;
    notEmpty_.notify_one();
    return true;
}

bool Queue::send(const void* item, uint32_t timeoutMs) { return put(item, timeoutMs, false); }
bool Queue::sendToFront(const void* item, uint32_t timeoutMs) { return put(item, timeoutMs, true); }

bool Queue::receive(void* item, uint32_t timeoutMs) {
    std::unique_lock<std::mutex> lk(m_);
    auto hasItem = [this] { return count_ > 0; };
    if (timeoutMs == kForever) {
        notEmpty_.wait(lk, hasItem);
    } else if (!notEmpty_.wait_for(lk, std::chrono::milliseconds(timeoutMs), hasItem)) {
        return false;
    }
    memcpy(item, buf_ + head_ * itemSize_, itemSize_);
    head_ = (head_ + 1) % capacity_;
    --count_;
    notFull_.notify_one();
    return true;
}

size_t Queue::waiting() const {
    std::lock_guard<std::mutex> lk(m_);
    return count_;
}

bool Queue::valid() const { return buf_ != nullptr; }

#endif

}  // namespace os
