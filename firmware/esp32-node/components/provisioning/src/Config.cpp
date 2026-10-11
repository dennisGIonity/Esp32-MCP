/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/Config.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "prov/Config.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

namespace prov {
namespace {

using Get = KvStore::Get;

const FieldInfo kFields[static_cast<size_t>(Field::Count)] = {
    {"ssid", false, true},        {"pass", true, true},         {"host_url", false, true},
    {"device_uuid", false, true}, {"ca_pem", false, true},      {"mqtt_user", false, true},
    {"mqtt_pass", true, true},    {"fleet_token", true, true},  {"mcp_token", true, true},
    {"ota_pass", true, true},     {"role", false, true},        {"site", false, true},
    {"group", false, true},       {"label", false, true},       {"server", false, false},
    {"mqtt_port", false, false},  {"http_port", false, false},  {"pins", false, false},
};

constexpr uint8_t kModeActive = 0;
constexpr uint8_t kModeLowPowerSleep = 3;
constexpr uint8_t kModeFailsafe = 4;

ConfigStore::CertCheck s_certCheck = nullptr;

/** Scratch for the NUL-terminated CA copy written to NVS. Guarded by ConfigStore::mu_. */
char s_caScratch[Limits::kCaPem + 1];

template <size_t N>
bool copyStr(char (&dst)[N], const char* s, size_t n) {
    if (n >= N) return false;
    memcpy(dst, s, n);
    dst[n] = '\0';
    return true;
}

bool isWholeNumber(double v, double lo, double hi) { return isfinite(v) && floor(v) == v && v >= lo && v <= hi; }

/** "mqtt://server:port" for the fw 2.x keys; false when server is not a usable host. */
bool hostUrlFromV1(const char* server, uint16_t port, char* out, size_t cap) {
    if (server[0] == '\0') {
        out[0] = '\0';  // empty = mDNS discovery, as in fw 2.x
        return true;
    }
    char tmp[96];
    const int w = snprintf(tmp, sizeof tmp, "mqtt://%s:%u", server, static_cast<unsigned>(port));
    if (w < 0 || static_cast<size_t>(w) >= sizeof tmp) return false;
    HostUrl u;
    const char* err = nullptr;
    if (!parseHostUrl(tmp, static_cast<size_t>(w), u, &err)) return false;
    if (strlen(u.href) >= cap) return false;
    strcpy(out, u.href);
    return true;
}

}  // namespace

const FieldInfo& fieldInfo(Field f) { return kFields[static_cast<size_t>(f) < static_cast<size_t>(Field::Count) ? static_cast<size_t>(f) : 0]; }

Field fieldByName(const char* name, size_t n) {
    for (size_t i = 0; i < static_cast<size_t>(Field::Count); ++i) {
        if (strlen(kFields[i].wire) == n && memcmp(kFields[i].wire, name, n) == 0) return static_cast<Field>(i);
    }
    return Field::Count;
}

bool NodeConfig::standalone() const { return strcmp(role, "standalone") == 0; }

const char* modeName(uint8_t mode) {
    switch (mode) {
        case 1: return "STANDBY";
        case 2: return "INFERENCE_ACTIVE";
        case 3: return "LOW_POWER_SLEEP";
        case 4: return "FAILSAFE";
        default: return "ACTIVE";
    }
}

void ConfigStore::setCertCheck(CertCheck fn) { s_certCheck = fn; }

bool ConfigStore::begin(KvStore& kv) {
    kv_ = &kv;
    return mu_.begin(false);
}

bool ConfigStore::load(LoadReport& rep) {
    os::Lock lk(mu_);
    NodeConfig c;  // defaults
    if (!kv_->open(true)) {
        rep.nvsError = true;
        cfg_ = c;
        return false;
    }
    // A value that does not fit its buffer (Get::Error) is treated as absent.
    auto str = [&](const char* key, char* out, size_t cap) {
        const Get g = kv_->getStr(key, out, cap);
        if (g == Get::Error) {
            rep.nvsError = true;
            out[0] = '\0';
        }
        return g;
    };
    str("wifi_ssid", c.ssid, sizeof c.ssid);
    str("wifi_pass", c.pass, sizeof c.pass);
    str("mqtt_user", c.mqttUser, sizeof c.mqttUser);
    str("mqtt_pass", c.mqttPass, sizeof c.mqttPass);
    str("fleet_token", c.fleetToken, sizeof c.fleetToken);
    str("mcp_token", c.mcpToken, sizeof c.mcpToken);
    str("ota_pass", c.otaPass, sizeof c.otaPass);
    str("server", c.server, sizeof c.server);
    if (kv_->getU16("mqtt_port", c.mqttPort) != Get::Ok || c.mqttPort == 0) c.mqttPort = 1883;
    if (kv_->getU16("http_port", c.httpPort) != Get::Ok || c.httpPort == 0) c.httpPort = 8099;

    // host_url: fw 3 key. Absent = first boot after fw 2.x -> migrate once (obligation 10).
    const Get hg = str("host_url", c.hostUrl, sizeof c.hostUrl);
    if (hg == Get::Missing) {
        if (!hostUrlFromV1(c.server, c.mqttPort, c.hostUrl, sizeof c.hostUrl)) {
            rep.hostUrlInvalid = true;  // fw 2.x accepted anything; fall back to mDNS
            c.hostUrl[0] = '\0';
        }
        rep.migratedHostUrl = c.server[0] != '\0';
        kv_->setStr("host_url", c.hostUrl);  // marks the migration as done, even when empty
    } else if (c.hostUrl[0] != '\0') {
        HostUrl u;
        const char* err = nullptr;
        if (!parseHostUrl(c.hostUrl, strlen(c.hostUrl), u, &err)) {
            rep.hostUrlInvalid = true;
            c.hostUrl[0] = '\0';
        }
    }

    char uuid[Limits::kUuid + 4] = {};
    if (str("dev_uuid", uuid, sizeof uuid) == Get::Ok && uuid[0] != '\0') {
        if (!normalizeUuid(uuid, strlen(uuid), c.deviceUuid)) c.deviceUuid[0] = '\0';
    }
    size_t caLen = 0;
    c.hasCa = kv_->strLen("ca_pem", caLen) == Get::Ok && caLen > 0;

    char role[16] = {};
    if (str("role", role, sizeof role) == Get::Ok && strcmp(role, "standalone") == 0) strcpy(c.role, "standalone");

    char raw[72] = {};
    if (str("site", raw, sizeof raw) == Get::Ok) {
        if (validTopicLevel(raw, strlen(raw))) {
            strcpy(c.site, raw);
        } else {
            sanitizeTopicLevel(raw, c.site, sizeof c.site, "lab");
            rep.siteSanitized = true;
        }
    }
    raw[0] = '\0';
    if (str("group", raw, sizeof raw) == Get::Ok) {
        if (validTopicLevel(raw, strlen(raw))) {
            strcpy(c.group, raw);
        } else {
            sanitizeTopicLevel(raw, c.group, sizeof c.group, "bench");
            rep.groupSanitized = true;
        }
    }
    str("label", c.label, sizeof c.label);
    if (hasControlChars(c.label, strlen(c.label))) c.label[0] = '\0';

    uint8_t mode = kModeActive;
    if (kv_->getU8("mode", mode) == Get::Ok) {
        if (mode == kModeLowPowerSleep || mode > kModeFailsafe) {  // woke up from sleep (fw 2.x rule)
            mode = kModeActive;
            rep.modeReset = true;
        }
    }
    c.mode = mode;
    kv_->getI32("pin_pwm0", c.pinPwm0);
    kv_->getI32("pin_pwm1", c.pinPwm1);
    kv_->getI32("pin_relay0", c.pinRelay0);

    uint32_t boots = 0;
    kv_->getU32("boot_count", boots);
    c.bootCount = boots + 1;
    kv_->setU32("boot_count", c.bootCount);

    const bool committed = kv_->commit();
    kv_->close();
    if (!committed) rep.nvsError = true;
    cfg_ = c;
    return !rep.nvsError;
}

void ConfigStore::snapshot(NodeConfig& out) const {
    os::Lock lk(mu_);
    out = cfg_;
}

bool ConfigStore::readCa(char* out, size_t cap) {
    os::Lock lk(mu_);
    if (!cfg_.hasCa || cap == 0) return false;
    bool ok = false;
    if (kv_->open(false)) {
        ok = kv_->getStr("ca_pem", out, cap) == Get::Ok && out[0] != '\0';
        kv_->close();
    }
    if (!ok) out[0] = '\0';
    return ok;
}

bool ConfigStore::validate(const SetItem& it, NodeConfig& s, const char** err, bool& hostFromV1) {
    const bool needsString = it.field != Field::MqttPort && it.field != Field::HttpPort && it.field != Field::Pins;
    if (needsString && !it.isString) {
        *err = "must be a string";
        return false;
    }
    const char* p = it.str;
    const size_t n = it.len;
    switch (it.field) {
        case Field::Ssid:
            if (!validSsid(p, n, err)) return false;
            return copyStr(s.ssid, p, n);
        case Field::Pass:
            if (!validWifiPass(p, n, err)) return false;
            return copyStr(s.pass, p, n);
        case Field::HostUrl: {
            if (n == 0) {
                s.hostUrl[0] = '\0';  // mDNS discovery of ionity-fleet.local
                return true;
            }
            HostUrl u;
            if (!parseHostUrl(p, n, u, err)) return false;
            if (!hostUrlSupported(u)) {
                *err = "this firmware connects over mqtt:// or mqtts:// only";
                return false;
            }
            if (strlen(u.href) >= sizeof s.hostUrl) {
                *err = "longer than 128 bytes";
                return false;
            }
            strcpy(s.hostUrl, u.href);
            if (u.scheme == Scheme::Mqtt && u.host[0] != '[' && strlen(u.host) < sizeof s.server) {
                strcpy(s.server, u.host);  // keep the fw 2.x keys in step (downgrade path)
                s.mqttPort = u.port;
            }
            return true;
        }
        case Field::DeviceUuid:
            if (n == 0) {
                s.deviceUuid[0] = '\0';
                return true;
            }
            if (!normalizeUuid(p, n, s.deviceUuid)) {
                *err = "device_uuid must be an RFC 9562 UUID";
                return false;
            }
            return true;
        case Field::CaPem:
            if (n == 0) {
                s.hasCa = false;
                return true;
            }
            if (!validPemCert(p, n, err)) return false;
            if (s_certCheck != nullptr) {
                memcpy(s_caScratch, p, n);
                s_caScratch[n] = '\0';
                if (!s_certCheck(s_caScratch, n)) {
                    *err = "not a valid X.509 certificate";
                    return false;
                }
            }
            s.hasCa = true;
            return true;
        case Field::MqttUser:
            if (!validText(p, n, Limits::kUser, err)) return false;
            return copyStr(s.mqttUser, p, n);
        case Field::MqttPass:
            if (!validText(p, n, Limits::kSecret, err)) return false;
            return copyStr(s.mqttPass, p, n);
        case Field::OtaPass:
            if (!validText(p, n, Limits::kSecret, err)) return false;
            return copyStr(s.otaPass, p, n);
        case Field::FleetToken:
        case Field::McpToken:
            if (n != 0 && !validToken(p, n)) {
                *err = "tokens are 16-128 characters: letters, digits, '.', '_', '~', '-'";
                return false;
            }
            return it.field == Field::FleetToken ? copyStr(s.fleetToken, p, n) : copyStr(s.mcpToken, p, n);
        case Field::Role:
            if (!((n == 4 && memcmp(p, "node", 4) == 0) || (n == 10 && memcmp(p, "standalone", 10) == 0))) {
                *err = "role must be node|standalone";
                return false;
            }
            return copyStr(s.role, p, n);
        case Field::Site:
        case Field::Group:
            if (!validTopicLevel(p, n)) {
                *err = "lowercase letters, digits and '-', 1-32 characters, starting with a letter or digit";
                return false;
            }
            return it.field == Field::Site ? copyStr(s.site, p, n) : copyStr(s.group, p, n);
        case Field::Label:
            if (!validText(p, n, Limits::kLabel, err)) return false;
            return copyStr(s.label, p, n);
        case Field::Server:
            if (n >= sizeof s.server || hasControlChars(p, n) || !validUtf8(p, n)) {
                *err = "server must be a host name or IPv4 address";
                return false;
            }
            copyStr(s.server, p, n);
            hostFromV1 = true;
            return true;
        case Field::MqttPort:
        case Field::HttpPort:
            if (!it.isNumber || !isWholeNumber(it.number, 1, 65535)) {
                *err = it.field == Field::MqttPort ? "mqtt_port must be 1-65535" : "http_port must be 1-65535";
                return false;
            }
            if (it.field == Field::MqttPort) {
                s.mqttPort = static_cast<uint16_t>(it.number);
                hostFromV1 = true;
            } else {
                s.httpPort = static_cast<uint16_t>(it.number);
            }
            return true;
        case Field::Pins:
            for (int k = 0; k < 3; ++k) {
                if (it.pins[k] != -2 && (it.pins[k] < -1 || it.pins[k] > 63)) {
                    *err = "pins must be GPIO numbers (-1 = unused)";
                    return false;
                }
            }
            if (it.pins[0] != -2) s.pinPwm0 = it.pins[0];
            if (it.pins[1] != -2) s.pinPwm1 = it.pins[1];
            if (it.pins[2] != -2) s.pinRelay0 = it.pins[2];
            return true;
        default:
            *err = "unknown field";
            return false;
    }
}

SetResult ConfigStore::apply(const SetItem* items, size_t n) {
    SetResult r;
    os::Lock lk(mu_);
    staged_ = cfg_;
    bool hostFromV1 = false;

    // 1. Validate everything; the first bad field aborts with nothing written.
    for (size_t i = 0; i < n; ++i) {
        const char* err = "invalid value";
        if (!validate(items[i], staged_, &err, hostFromV1)) {
            r.badField = items[i].field;
            r.error = err;
            return r;
        }
        r.changed |= fieldBit(items[i].field);
    }
    if (hostFromV1 && !hostUrlFromV1(staged_.server, staged_.mqttPort, staged_.hostUrl, sizeof staged_.hostUrl)) {
        r.badField = Field::Server;
        r.error = "server must be a host name or IPv4 address";
        r.changed = 0;
        return r;
    }
    if (n == 0) {
        r.ok = true;
        return r;
    }

    // 2. One handle, every key, one commit.
    if (!kv_->open(true)) {
        r.storageError = true;
        r.error = "could not open the settings storage";
        r.changed = 0;
        return r;
    }
    bool ok = true;
    for (size_t i = 0; i < n && ok; ++i) {
        const NodeConfig& s = staged_;
        switch (items[i].field) {
            case Field::Ssid: ok = kv_->setStr("wifi_ssid", s.ssid); break;
            case Field::Pass: ok = kv_->setStr("wifi_pass", s.pass); break;
            case Field::HostUrl:
                ok = kv_->setStr("host_url", s.hostUrl);
                if (ok && strncmp(s.hostUrl, "mqtt://", 7) == 0) {
                    ok = kv_->setStr("server", s.server) && kv_->setU16("mqtt_port", s.mqttPort);
                }
                break;
            case Field::DeviceUuid: ok = kv_->setStr("dev_uuid", s.deviceUuid); break;
            case Field::CaPem:
                if (items[i].len == 0) {
                    ok = kv_->erase("ca_pem");
                } else {
                    memcpy(s_caScratch, items[i].str, items[i].len);
                    s_caScratch[items[i].len] = '\0';
                    ok = kv_->setStr("ca_pem", s_caScratch);
                }
                break;
            case Field::MqttUser: ok = kv_->setStr("mqtt_user", s.mqttUser); break;
            case Field::MqttPass: ok = kv_->setStr("mqtt_pass", s.mqttPass); break;
            case Field::FleetToken: ok = kv_->setStr("fleet_token", s.fleetToken); break;
            case Field::McpToken: ok = kv_->setStr("mcp_token", s.mcpToken); break;
            case Field::OtaPass: ok = kv_->setStr("ota_pass", s.otaPass); break;
            case Field::Role: ok = kv_->setStr("role", s.role); break;
            case Field::Site: ok = kv_->setStr("site", s.site); break;
            case Field::Group: ok = kv_->setStr("group", s.group); break;
            case Field::Label: ok = kv_->setStr("label", s.label); break;
            case Field::Server: ok = kv_->setStr("server", s.server); break;
            case Field::MqttPort: ok = kv_->setU16("mqtt_port", s.mqttPort); break;
            case Field::HttpPort: ok = kv_->setU16("http_port", s.httpPort); break;
            case Field::Pins:
                ok = kv_->setI32("pin_pwm0", s.pinPwm0) && kv_->setI32("pin_pwm1", s.pinPwm1) &&
                     kv_->setI32("pin_relay0", s.pinRelay0);
                break;
            default: break;
        }
    }
    if (ok && hostFromV1) ok = kv_->setStr("host_url", staged_.hostUrl);
    ok = ok && kv_->commit();
    kv_->close();
    if (!ok) {
        r.storageError = true;
        r.error = "could not write the settings to flash";
        r.changed = 0;
        return r;
    }
    cfg_ = staged_;
    r.ok = true;
    return r;
}

bool ConfigStore::eraseAll() {
    os::Lock lk(mu_);
    if (!kv_->open(true)) return false;
    const bool ok = kv_->eraseAll() && kv_->commit();
    kv_->close();
    if (ok) {
        const uint32_t boots = cfg_.bootCount;
        cfg_ = NodeConfig{};
        cfg_.bootCount = boots;
    }
    return ok;
}

bool ConfigStore::saveMode(uint8_t mode) {
    os::Lock lk(mu_);
    if (!kv_->open(true)) return false;
    const bool ok = kv_->setU8("mode", mode) && kv_->commit();
    kv_->close();
    if (ok) cfg_.mode = mode;
    return ok;
}

}  // namespace prov
