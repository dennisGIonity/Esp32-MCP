/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | mcp/os.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The few RTOS primitives the MCP server needs, behind one small interface:
 *   ESP_PLATFORM  -> FreeRTOS queues/tasks, esp_timer, heap_caps, Task WDT
 *   host build    -> std::mutex / std::condition_variable / std::thread
 * so MCPServer.cpp is compiled unchanged for the device and for the host
 * tests (ASan, UBSan and TSan exercise the real routing code).
 *
 * MEMORY-SAFETY / RTOS rules encoded here:
 *  - Queue::begin() and allocPool() allocate. Call them ONLY during boot,
 *    never from a frame handler (Phase 1 firmware rule 1: no hot-path malloc).
 *  - Queues copy items by value (memcpy). Only put trivially copyable types in
 *    them (TypedQueue enforces it at compile time) - never a pointer to a stack
 *    object of the sender.
 *  - A timeout of 0 never blocks; that is what the network task uses.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <type_traits>

#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#else
#include <condition_variable>
#include <mutex>
#endif

namespace os {

/** Wait forever (use only where the task has nothing else to do and no WDT duty). */
constexpr uint32_t kForever = UINT32_MAX;

/** Monotonic microseconds since boot (esp_timer_get_time on the device). */
int64_t nowUs();

/** Hardware RNG on the device (esp_random), a seeded PRNG on the host. */
uint32_t random32();

void delayMs(uint32_t ms);

/**
 * Boot-time pool allocation. `preferPsram` places the block in external RAM
 * when the chip has it (ESP32-S3 N8R8/N16R8) and falls back to internal RAM
 * (ESP32-C6). Returns nullptr on failure - callers must refuse to start.
 * MEMORY-SAFETY: never free these blocks; they live for the whole uptime.
 */
void* allocPool(size_t bytes, bool preferPsram);

/** Return a pool block. Only the host tests do this (device pools live for the whole uptime). */
void freePool(void* p);

/** Free internal RAM in bytes (heap_caps_get_free_size(MALLOC_CAP_INTERNAL)); 0 on host. */
uint32_t internalFreeBytes();

/** Task watchdog: subscribe the calling task / feed it. No-ops on the host. */
void wdtSubscribe();
void wdtReset();
void wdtUnsubscribe();

using TaskFn = void (*)(void* arg);

/**
 * Start a task. core = 0 or 1 pins it (dual-core ESP32-S3), core < 0 leaves it
 * unpinned (single-core ESP32-C6, or don't care). On the host the thread is
 * detached; `stackBytes`, `priority` and `core` are ignored there.
 * `handle` (optional) receives the TaskHandle_t (void* on the host: nullptr).
 */
bool spawn(const char* name, TaskFn fn, void* arg, uint32_t stackBytes, unsigned priority, int core,
           void** handle);

/** End the calling task. A FreeRTOS task function must never return; call this instead. */
void exitTask();

/* ------------------------------------------------------------------------- */
/* Mutex (optionally recursive) with a timeout                                */
/* ------------------------------------------------------------------------- */

class Mutex {
public:
    Mutex() = default;
    Mutex(const Mutex&) = delete;
    Mutex& operator=(const Mutex&) = delete;
    ~Mutex();

    /** Creates the mutex. Boot only (it allocates a small control block on the device). */
    bool begin(bool recursive = false);
    /** False on timeout. Never call with a timeout from an ISR. */
    bool lock(uint32_t timeoutMs = kForever);
    void unlock();

private:
    bool recursive_ = false;
#ifdef ESP_PLATFORM
    SemaphoreHandle_t m_ = nullptr;
#else
    std::recursive_timed_mutex m_;
#endif
};

/** RAII lock; check owns() when a timeout was given. */
class Lock {
public:
    explicit Lock(Mutex& m, uint32_t timeoutMs = kForever) : m_(m), owns_(m.lock(timeoutMs)) {}
    ~Lock() {
        if (owns_) m_.unlock();
    }
    Lock(const Lock&) = delete;
    Lock& operator=(const Lock&) = delete;
    bool owns() const { return owns_; }

private:
    Mutex& m_;
    bool owns_;
};

/* ------------------------------------------------------------------------- */
/* Queue: fixed-capacity FIFO of fixed-size items, copy-in / copy-out          */
/* ------------------------------------------------------------------------- */

class Queue {
public:
    Queue() = default;
    Queue(const Queue&) = delete;
    Queue& operator=(const Queue&) = delete;
    ~Queue();

    /** Allocates the storage. Boot only. */
    bool begin(size_t itemSize, size_t capacity);

    bool send(const void* item, uint32_t timeoutMs);
    bool sendToFront(const void* item, uint32_t timeoutMs);
    bool receive(void* item, uint32_t timeoutMs);
    size_t waiting() const;
    size_t capacity() const { return capacity_; }
    bool valid() const;

private:
    size_t itemSize_ = 0;
    size_t capacity_ = 0;
#ifdef ESP_PLATFORM
    QueueHandle_t q_ = nullptr;
#else
    bool put(const void* item, uint32_t timeoutMs, bool front);
    mutable std::mutex m_;
    std::condition_variable notEmpty_;
    std::condition_variable notFull_;
    uint8_t* buf_ = nullptr;
    size_t head_ = 0;
    size_t count_ = 0;
#endif
};

/** Type-safe wrapper: only trivially copyable items may cross a task boundary by value. */
template <typename T>
class TypedQueue {
    static_assert(std::is_trivially_copyable<T>::value, "queue items are copied with memcpy");

public:
    bool begin(size_t capacity) { return q_.begin(sizeof(T), capacity); }
    bool send(const T& v, uint32_t timeoutMs) { return q_.send(&v, timeoutMs); }
    bool sendToFront(const T& v, uint32_t timeoutMs) { return q_.sendToFront(&v, timeoutMs); }
    bool receive(T& v, uint32_t timeoutMs) { return q_.receive(&v, timeoutMs); }
    size_t waiting() const { return q_.waiting(); }
    size_t capacity() const { return q_.capacity(); }
    bool valid() const { return q_.valid(); }

private:
    Queue q_;
};

}  // namespace os
