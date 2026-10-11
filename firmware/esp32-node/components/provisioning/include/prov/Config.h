/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/Config.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The node configuration: NVS namespace "ionity", key-compatible with fw 2.x
 * (Arduino Preferences), so a board updated over USB with "keep settings"
 * comes up on its Wi-Fi with no re-provisioning.
 *
 *   wire field     NVS key      type   notes
 *   ssid           wifi_ssid    str
 *   pass           wifi_pass    str    secret
 *   host_url       host_url     str    fw 3; migrated from server + mqtt_port
 *   device_uuid    dev_uuid     str    fw 3
 *   ca_pem         ca_pem       str    fw 3, <= 3072 B (kept out of RAM until the MQTT client needs it)
 *   mqtt_user      mqtt_user    str
 *   mqtt_pass      mqtt_pass    str    secret
 *   fleet_token    fleet_token  str    secret
 *   mcp_token      mcp_token    str    secret
 *   ota_pass       ota_pass     str    secret
 *   role/site/group/label       str
 *   server         server       str    fw 2.x (prov/1 tools); kept for downgrades
 *   mqtt_port      mqtt_port    u16    fw 2.x  (Preferences putUShort)
 *   http_port      http_port    u16    fw 2.x
 *   pins{...}      pin_pwm0 ..  i32    fw 2.x  (Preferences putInt)
 *   -              mode         u8     StateMode (fw 2.x putUChar)
 *   -              boot_count   u32    fw 3
 *
 * MEMORY-SAFETY: every string has a fixed buffer of limit + 1 bytes; NVS
 * values that do not fit (corruption, a foreign writer) are treated as
 * absent and logged, never truncated silently into something else.
 * THREADS: the in-RAM copy is guarded by a mutex; readers take snapshot()
 * copies, so no task ever reads a field while `set` rewrites it.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "mcp/os.h"
#include "prov/Validate.h"

namespace prov {

enum class Field : uint8_t {
    Ssid,
    Pass,
    HostUrl,
    DeviceUuid,
    CaPem,
    MqttUser,
    MqttPass,
    FleetToken,
    McpToken,
    OtaPass,
    Role,
    Site,
    Group,
    Label,
    // prov/1 (fw 2.x tools: flasher 2.0, provision.py)
    Server,
    MqttPort,
    HttpPort,
    Pins,
    Count
};

struct FieldInfo {
    const char* wire;   // JSON name in `set`
    bool secret;        // never echoed, logged or reported except as has_*
    bool advertised;    // listed in hello "set_fields" (prov/2)
};

const FieldInfo& fieldInfo(Field f);
/** Field by wire name, or Field::Count. */
Field fieldByName(const char* name, size_t n);

constexpr uint32_t fieldBit(Field f) { return 1u << static_cast<uint8_t>(f); }

struct NodeConfig {
    char ssid[Limits::kSsid + 1] = {};
    char pass[Limits::kPskHex + 1] = {};
    char hostUrl[160] = {};
    char deviceUuid[Limits::kUuid + 1] = {};
    bool hasCa = false;
    char mqttUser[Limits::kUser + 1] = {};
    char mqttPass[Limits::kSecret + 1] = {};
    char fleetToken[Limits::kSecret + 1] = {};
    char mcpToken[Limits::kSecret + 1] = {};
    char otaPass[Limits::kSecret + 1] = {};
    char role[12] = "node";
    char site[Limits::kTopicLevel + 1] = "lab";
    char group[Limits::kTopicLevel + 1] = "bench";
    char label[Limits::kLabel + 1] = {};
    // fw 2.x connection keys, kept in step with an mqtt:// host_url so a downgrade keeps working
    char server[64] = {};
    uint16_t mqttPort = 1883;
    uint16_t httpPort = 8099;
    uint8_t mode = 0;  // StateMode
    int32_t pinPwm0 = -1;
    int32_t pinPwm1 = -1;
    int32_t pinRelay0 = -1;
    uint32_t bootCount = 0;

    bool provisioned() const { return ssid[0] != '\0'; }
    bool standalone() const;
};

/** NVS namespace access (ESP: nvs_* on namespace "ionity"; host tests: a map). */
class KvStore {
public:
    enum class Get : uint8_t { Ok, Missing, Error };
    virtual bool open(bool write) = 0;
    virtual void close() = 0;
    /** NUL-terminated into out[cap]; a value that does not fit is Error (never truncated). */
    virtual Get getStr(const char* key, char* out, size_t cap) = 0;
    /** Length without the NUL; Missing when absent. */
    virtual Get strLen(const char* key, size_t& len) = 0;
    virtual Get getU8(const char* key, uint8_t& v) = 0;
    virtual Get getU16(const char* key, uint16_t& v) = 0;
    virtual Get getU32(const char* key, uint32_t& v) = 0;
    virtual Get getI32(const char* key, int32_t& v) = 0;
    virtual bool setStr(const char* key, const char* v) = 0;
    virtual bool setU8(const char* key, uint8_t v) = 0;
    virtual bool setU16(const char* key, uint16_t v) = 0;
    virtual bool setU32(const char* key, uint32_t v) = 0;
    virtual bool setI32(const char* key, int32_t v) = 0;
    /** Missing keys are not an error. */
    virtual bool erase(const char* key) = 0;
    virtual bool eraseAll() = 0;
    virtual bool commit() = 0;

protected:
    ~KvStore() = default;
};

/** One `set` field as parsed from the request (views into the parsed JSON). */
struct SetItem {
    Field field = Field::Count;
    const char* str = nullptr;  // string fields
    size_t len = 0;
    bool isString = false;
    bool isNumber = false;      // mqtt_port / http_port
    double number = 0;
    // pins{pwm0,pwm1,relay0}: -2 = not given
    int32_t pins[3] = {-2, -2, -2};
};

struct SetResult {
    bool ok = false;
    Field badField = Field::Count;
    const char* error = nullptr;  // static text
    uint32_t changed = 0;         // fieldBit() of every field written
    bool storageError = false;
};

struct LoadReport {
    bool migratedHostUrl = false;  // fw 2.x server + mqtt_port -> host_url
    bool hostUrlInvalid = false;   // stored host_url could not be parsed (ignored)
    bool siteSanitized = false;
    bool groupSanitized = false;
    bool modeReset = false;        // woke from LOW_POWER_SLEEP / unknown mode
    bool nvsError = false;
};

class ConfigStore {
public:
    /** Boot: mutex + backend. */
    bool begin(KvStore& kv);

    /** Boot: read everything, migrate fw 2.x settings, count the boot. */
    bool load(LoadReport& report);

    /** Thread-safe copy of the current configuration. */
    void snapshot(NodeConfig& out) const;

    /** The stored CA certificate (NUL-terminated) into out[cap]; false when none. */
    bool readCa(char* out, size_t cap);

    /**
     * `set`: validate EVERY item first; on the first bad one return it and
     * write nothing. Otherwise write all keys through one NVS handle, commit
     * once, then update the in-RAM copy. Unknown-field policy (prov/2 strict,
     * prov/1 lenient) is the caller's: only known fields reach this point.
     */
    SetResult apply(const SetItem* items, size_t n);

    /** factory_reset: erase the whole namespace and reset RAM to defaults. */
    bool eraseAll();

    /** Persist the StateMode (FAILSAFE is latched across reboots, fw 2.x behaviour). */
    bool saveMode(uint8_t mode);

    /**
     * Optional deep check of a PEM certificate (the device plugs in an
     * mbedTLS X.509 parse). Without it only the PEM structure is checked.
     */
    using CertCheck = bool (*)(const char* pemNulTerminated, size_t len);
    static void setCertCheck(CertCheck fn);

private:
    bool validate(const SetItem& it, NodeConfig& staged, const char** err, bool& hostFromV1);

    KvStore* kv_ = nullptr;
    mutable os::Mutex mu_;
    NodeConfig cfg_;
    NodeConfig staged_;  // apply() scratch, guarded by mu_
};

/** StateMode name as fw 2.x printed it ("ACTIVE", "FAILSAFE", ...). */
const char* modeName(uint8_t mode);

}  // namespace prov
