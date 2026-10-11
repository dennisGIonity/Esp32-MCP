/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/ProvHandler.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The board side of ionity-prov/2 (docs/architecture/PHASE-2-PORTAL.md §4-5),
 * portable: the device wires it to the console and Wi-Fi/MQTT; the host tests
 * and the TypeScript interop test drive the very same code through a pipe.
 *
 *   host  -> {"ionity":"prov","op":"set","v":2,"id":7,"ssid":"..."} *1a2b3c4d
 *   board -> IONITY-PROV {"ok":true,"op":"set","id":7,"changed":["ssid"],...} *5e6f7a8b
 *
 * Obligations implemented here (numbers from PHASE-2-PORTAL.md §5):
 *   1  announce()                       hello reply at boot, unprompted
 *   2  prov/1 and prov/2 envelopes      for every op (no "v" = lenient prov/1 rules)
 *   3  CRC both ways                    verify when present, mismatch -> error, no action;
 *                                       EVERY reply carries the suffix; id echoed
 *   4  line_max 4096                    the console calls onOverlong() past it
 *   5  atomic replies                   one WriteFn call per reply line
 *   6  validate, then write             ConfigStore::apply(); unknown field -> error + field (prov/2)
 *   7  no secrets out                   only has_* booleans; secret strings wiped after parsing
 *   8  test without reboot              ProvBackend::startTest() on a worker, one reply via finishTest()
 *   9  reboot / factory_reset           reply first, then ProvBackend::restart()
 *  10  fw 2.x migration                 ConfigStore::load()
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "mcp/os.h"
#include "prov/Config.h"
#include "prov/Json.h"

struct cJSON;

namespace prov {

/** Fixed for the uptime (filled at boot). */
struct BoardStatic {
    char hwid[24] = {};
    char fw[17] = {};
    char chip[16] = {};
    uint16_t chipRev = 0;
    uint8_t flashMb = 0;
    char mac[18] = {};
};

/** Live link state, asked from the backend when a reply is built. */
struct LiveState {
    bool wifiUp = false;
    char ip[40] = {};
    int rssi = 0;
    char wifiError[80] = {};
    bool mqttUp = false;
    uint8_t mode = 0;
};

struct TestOutcome {
    bool wifiOk = false;
    char ip[40] = {};
    int rssi = 0;
    char wifiError[80] = {};
    bool brokerReported = false;  // false for role=standalone (no MCP host to reach)
    bool brokerOk = false;
    char brokerDetail[96] = {};
};

struct ScanOutcome {
    static constexpr size_t kMax = 20;
    struct Net {
        char ssid[33];
        uint8_t ssidLen;  // SSIDs are bytes, not text: may contain NULs or non-UTF-8
        int8_t rssi;
        uint8_t ch;
        bool open;
    };
    bool ok = false;
    const char* error = nullptr;
    uint8_t count = 0;
    Net nets[kMax] = {};
};

/** Platform services. All calls come from the console task unless noted. */
class ProvBackend {
public:
    virtual void live(LiveState& out) = 0;
    /** Start a scan on a worker; the result comes back through ProvHandler::finishScan(). False = busy. */
    virtual bool startScan() = 0;
    /** Join with the STORED settings and reach the host, bounded by timeoutMs; result via finishTest(). */
    virtual bool startTest(uint32_t timeoutMs) = 0;
    /** After a successful `set`: apply what can be applied live (Wi-Fi), refresh secret scrubbing. */
    virtual void configChanged(uint32_t changedFields) = 0;
    /** factory_reset: forget the Wi-Fi driver's own stored config (esp_wifi_restore). */
    virtual bool factoryResetWifi() = 0;
    /** Flush the console, wait >= 100 ms, restart. Never returns on the device. */
    virtual void restart() = 0;

protected:
    ~ProvBackend() = default;
};

class ProvHandler {
public:
    static constexpr size_t kLineMax = 4096;
    static constexpr size_t kReplyMax = 3072;
    static constexpr const char* kPrefix = "IONITY-PROV ";

    using WriteFn = void (*)(void* user, const char* data, size_t len);

    bool begin(ConfigStore& cfg, ProvBackend& backend, const BoardStatic& board, WriteFn write, void* writeUser);

    /** Boot announcement (obligation 1). */
    void announce();

    /**
     * One complete line from the host, without the trailing "\r\n".
     * Lines that do not start with '{' are ordinary input and are ignored.
     */
    void onLine(const char* line, size_t len);

    /** The console dropped a line longer than kLineMax (obligation 4). */
    void onOverlong();

    /** Completion of startScan()/startTest(). Any task (the backend's worker). */
    void finishScan(const ScanOutcome& r);
    void finishTest(const TestOutcome& r);

    /** For tests: a long operation is running. */
    bool busy() const;

private:
    enum class LongOp : uint8_t { None, Scan, Test };
    struct Ctx {
        bool v2 = false;
        bool hasId = false;
        int64_t id = 0;
    };

    void dispatch(const cJSON* req, const char* op, const Ctx& ctx);
    void handleSet(const cJSON* req, const Ctx& ctx);
    void startLong(LongOp op, const char* opName, const Ctx& ctx, uint32_t timeoutMs);

    // Reply building. Call with mu_ held.
    JsonWriter& begin(bool ok, const char* op, const Ctx* ctx);
    void send();
    void sendError(const char* op, const Ctx* ctx, const char* error, const char* field = nullptr);
    /** mqttOverride: -1 = live state, 0/1 = report this instead (prov/1 test reply). */
    void writeInfo(const NodeConfig& c, const LiveState& live, int mqttOverride = -1);

    ConfigStore* cfg_ = nullptr;
    ProvBackend* backend_ = nullptr;
    BoardStatic board_;
    WriteFn write_ = nullptr;
    void* writeUser_ = nullptr;

    mutable os::Mutex mu_;  // reply buffer, pending long op, config snapshot scratch
    char out_[kReplyMax] = {};
    JsonWriter w_{nullptr, 0};
    NodeConfig snap_;
    LongOp pending_ = LongOp::None;
    Ctx pendingCtx_;
};

}  // namespace prov
