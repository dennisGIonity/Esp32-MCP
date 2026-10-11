/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/prov_fakes.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Host stand-ins for NVS and the Wi-Fi/MQTT backend of the provisioning
 * protocol. The networks and their passwords mirror FakeBoard in
 * packages/provisioning/src/testing/fake-board.ts, so the TypeScript client
 * sees the same world from the C++ firmware code as from its own simulator.
 */
#pragma once

#include <stdio.h>
#include <string.h>

#include <map>
#include <string>
#include <vector>

#include "prov/Config.h"
#include "prov/ProvHandler.h"

namespace pf {

/** NVS namespace "ionity" as a typed map (type mismatches behave like NVS: an error). */
class MemKv final : public prov::KvStore {
public:
    struct Value {
        char type;  // 's', '1' (u8), '2' (u16), '4' (u32), 'i' (i32)
        std::string s;
        int64_t n = 0;
    };
    std::map<std::string, Value> data;
    int commits = 0;
    bool failWrites = false;
    bool opened = false;

    bool open(bool) override {
        opened = true;
        return true;
    }
    void close() override { opened = false; }

    Get getStr(const char* key, char* out, size_t cap) override {
        auto it = data.find(key);
        if (it == data.end()) return Get::Missing;
        if (it->second.type != 's' || it->second.s.size() + 1 > cap) return Get::Error;
        memcpy(out, it->second.s.c_str(), it->second.s.size() + 1);
        return Get::Ok;
    }
    Get strLen(const char* key, size_t& len) override {
        auto it = data.find(key);
        if (it == data.end()) return Get::Missing;
        if (it->second.type != 's') return Get::Error;
        len = it->second.s.size();
        return Get::Ok;
    }
    template <typename T>
    Get getNum(const char* key, char type, T& v) {
        auto it = data.find(key);
        if (it == data.end()) return Get::Missing;
        if (it->second.type != type) return Get::Error;
        v = static_cast<T>(it->second.n);
        return Get::Ok;
    }
    Get getU8(const char* k, uint8_t& v) override { return getNum(k, '1', v); }
    Get getU16(const char* k, uint16_t& v) override { return getNum(k, '2', v); }
    Get getU32(const char* k, uint32_t& v) override { return getNum(k, '4', v); }
    Get getI32(const char* k, int32_t& v) override { return getNum(k, 'i', v); }

    bool setStr(const char* key, const char* v) override {
        if (failWrites || strlen(key) > 15) return false;  // NVS_KEY_NAME_MAX_SIZE - 1
        data[key] = Value{'s', v, 0};
        return true;
    }
    bool setNum(const char* key, char type, int64_t v) {
        if (failWrites || strlen(key) > 15) return false;
        data[key] = Value{type, "", v};
        return true;
    }
    bool setU8(const char* k, uint8_t v) override { return setNum(k, '1', v); }
    bool setU16(const char* k, uint16_t v) override { return setNum(k, '2', v); }
    bool setU32(const char* k, uint32_t v) override { return setNum(k, '4', v); }
    bool setI32(const char* k, int32_t v) override { return setNum(k, 'i', v); }
    bool erase(const char* key) override {
        data.erase(key);
        return true;
    }
    bool eraseAll() override {
        data.clear();
        return true;
    }
    bool commit() override {
        ++commits;
        return !failWrites;
    }

    std::string str(const char* key) const {
        auto it = data.find(key);
        return it == data.end() ? std::string("<missing>") : it->second.s;
    }
    int64_t num(const char* key) const {
        auto it = data.find(key);
        return it == data.end() ? -1 : it->second.n;
    }
};

struct FakeNet {
    const char* ssid;
    int rssi;
    int ch;
    const char* pass;  // nullptr = open
};

/** Wi-Fi + broker world of FakeBoard (fake-board.ts), completing operations synchronously. */
class FakeBackend final : public prov::ProvBackend {
public:
    prov::ProvHandler* handler = nullptr;
    prov::ConfigStore* cfg = nullptr;
    std::vector<FakeNet> nets = {
        {"Afrihost Fibre DTM", -44, 11, "correct-horse"},
        {"IONITY-LAB-IOT", -61, 6, "lab-pass-123"},
        {"Guest", -78, 1, nullptr},
    };
    bool brokerReachable = true;
    bool wifiUp = false;
    bool mqttUp = false;
    bool radioBusy = false;
    int restarts = 0;
    int wifiResets = 0;
    uint32_t lastTestTimeout = 0;
    uint32_t lastChanged = 0;
    bool includeOddSsid = false;  // adds a non-UTF-8 SSID to scans

    void live(prov::LiveState& out) override {
        out.wifiUp = wifiUp;
        if (wifiUp) {
            snprintf(out.ip, sizeof out.ip, "192.168.0.42");
            out.rssi = -61;
        }
        out.mqttUp = mqttUp;
        out.mode = 0;
    }

    bool startScan() override {
        if (radioBusy) return false;
        prov::ScanOutcome r;
        r.ok = true;
        for (const FakeNet& n : nets) {
            prov::ScanOutcome::Net& o = r.nets[r.count++];
            o.ssidLen = static_cast<uint8_t>(strlen(n.ssid));
            memcpy(o.ssid, n.ssid, o.ssidLen);
            o.rssi = static_cast<int8_t>(n.rssi);
            o.ch = static_cast<uint8_t>(n.ch);
            o.open = n.pass == nullptr;
        }
        if (includeOddSsid) {
            prov::ScanOutcome::Net& o = r.nets[r.count++];
            const char odd[] = {'c', 'a', 'f', static_cast<char>(0xE9), '\0'};  // Latin-1 "café"
            memcpy(o.ssid, odd, 4);
            o.ssidLen = 4;
            o.rssi = -80;
            o.ch = 3;
            o.open = true;
        }
        handler->finishScan(r);
        return true;
    }

    bool startTest(uint32_t timeoutMs) override {
        if (radioBusy) return false;
        lastTestTimeout = timeoutMs;
        prov::NodeConfig c;
        cfg->snapshot(c);
        prov::TestOutcome r;
        const FakeNet* net = nullptr;
        for (const FakeNet& n : nets) {
            if (strcmp(n.ssid, c.ssid) == 0) net = &n;
        }
        if (net == nullptr) {
            snprintf(r.wifiError, sizeof r.wifiError, "SSID not found (2.4 GHz only)");
        } else if (strcmp(net->pass ? net->pass : "", c.pass) != 0) {
            snprintf(r.wifiError, sizeof r.wifiError, "wrong password (auth/4-way handshake failed)");
        } else {
            r.wifiOk = true;
            wifiUp = true;
            snprintf(r.ip, sizeof r.ip, "192.168.0.42");
            r.rssi = net->rssi;
        }
        if (!r.wifiOk) {
            wifiUp = false;
            mqttUp = false;
            r.brokerReported = !c.standalone();
            snprintf(r.brokerDetail, sizeof r.brokerDetail, "no Wi-Fi");
        } else if (c.standalone()) {
            snprintf(r.brokerDetail, sizeof r.brokerDetail, "standalone: no MCP host");
        } else {
            r.brokerReported = true;
            r.brokerOk = brokerReachable;
            mqttUp = brokerReachable;
            snprintf(r.brokerDetail, sizeof r.brokerDetail, "%s %.70s",
                     brokerReachable ? "connected to" : "cannot reach",
                     c.hostUrl[0] ? c.hostUrl : "mqtt://ionity-fleet.local:1883");
        }
        handler->finishTest(r);
        return true;
    }

    void configChanged(uint32_t changed) override {
        lastChanged = changed;
        if (changed & (prov::fieldBit(prov::Field::Ssid) | prov::fieldBit(prov::Field::Pass))) {
            wifiUp = false;
            mqttUp = false;
        }
    }
    bool factoryResetWifi() override {
        ++wifiResets;
        return true;
    }
    void restart() override {
        ++restarts;
        wifiUp = false;
        mqttUp = false;
    }
};

inline prov::BoardStatic board() {
    prov::BoardStatic b;
    snprintf(b.hwid, sizeof b.hwid, "esp32-98a316e5d18c");
    snprintf(b.fw, sizeof b.fw, "3.0.0");
    snprintf(b.chip, sizeof b.chip, "ESP32-S3");
    b.chipRev = 2;
    b.flashMb = 16;
    snprintf(b.mac, sizeof b.mac, "98:a3:16:e5:d1:8c");
    return b;
}

}  // namespace pf
