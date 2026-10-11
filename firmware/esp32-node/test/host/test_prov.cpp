/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/test_prov.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * ionity-prov/2 board side: framing, CRC, validation-then-write, fw 2.x
 * migration, long operations, secrets hygiene, line assembly, scrubbing.
 */
#include <string.h>

#include <string>
#include <vector>

#include "cJSON.h"
#include "check.h"
#include "ewp/crc32.h"
#include "prov/LineAssembler.h"
#include "prov/ProvHandler.h"
#include "prov/Scrubber.h"
#include "prov/Validate.h"
#include "prov_fakes.h"

namespace {

struct ProvRig {
    pf::MemKv kv;
    prov::ConfigStore cfg;
    pf::FakeBackend be;
    prov::ProvHandler h;
    std::vector<std::string> out;  // raw reply lines
    prov::LoadReport report;

    explicit ProvRig(bool load = true) {
        CHECK(cfg.begin(kv));
        if (load) cfg.load(report);
        be.handler = &h;
        be.cfg = &cfg;
        CHECK(h.begin(cfg, be, pf::board(), &ProvRig::write, this));
    }
    static void write(void* self, const char* data, size_t len) {
        static_cast<ProvRig*>(self)->out.emplace_back(data, len);
    }
    /** Send a request line (newline excluded), prov/2 style with CRC when crc = true. */
    void send(const std::string& json, bool crc = true) {
        std::string line = json;
        if (crc) {
            char hex[9];
            ewp::crc32_hex(ewp::crc32(json.data(), json.size()), hex);
            line += " *";
            line += hex;
        }
        h.onLine(line.data(), line.size());
    }
    /** Last reply, CRC-checked and parsed (caller frees). */
    cJSON* last() {
        if (out.empty()) return nullptr;
        return parse(out.back());
    }
    static cJSON* parse(const std::string& raw) {
        const std::string prefix = "IONITY-PROV ";
        if (raw.compare(0, prefix.size(), prefix) != 0 || raw.back() != '\n') return nullptr;
        const std::string body = raw.substr(prefix.size(), raw.size() - prefix.size() - 1);
        if (body.size() < 11 || body.compare(body.size() - 10, 2, " *") != 0) return nullptr;
        const std::string json = body.substr(0, body.size() - 10);
        uint32_t want = 0;
        if (!ewp::crc32_parse_hex(body.c_str() + body.size() - 8, &want)) return nullptr;
        if (ewp::crc32(json.data(), json.size()) != want) return nullptr;
        return cJSON_Parse(json.c_str());
    }
};

struct Json {
    cJSON* j;
    explicit Json(cJSON* p) : j(p) {}
    ~Json() { cJSON_Delete(j); }
    const cJSON* get(const char* k) const { return j ? cJSON_GetObjectItemCaseSensitive(j, k) : nullptr; }
    bool is(const char* k, bool v) const { return cJSON_IsBool(get(k)) && (cJSON_IsTrue(get(k)) == v); }
    std::string str(const char* k) const {
        const cJSON* v = get(k);
        return (v != nullptr && cJSON_IsString(v) && v->valuestring != nullptr) ? v->valuestring : "<none>";
    }
    double num(const char* k) const {
        const cJSON* v = get(k);
        return (v != nullptr && cJSON_IsNumber(v)) ? v->valuedouble : -12345;
    }
};

}  // namespace

TEST(prov_hello_is_v2_with_crc_and_no_secrets) {
    ProvRig rig;
    rig.kv.setStr("wifi_pass", "s3cret-pass");
    rig.h.announce();
    CHECK_EQ(rig.out.size(), 1u);
    Json j(rig.last());
    CHECK(j.j != nullptr);  // CRC verified inside parse()
    CHECK(j.is("ok", true));
    CHECK_STR(j.str("op").c_str(), "hello");
    CHECK_EQ(j.num("prov"), 2);
    CHECK_EQ(j.num("line_max"), 4096);
    CHECK_STR(j.str("hwid").c_str(), "esp32-98a316e5d18c");
    CHECK_STR(j.str("device_id").c_str(), "esp32-98a316e5d18c");
    CHECK_STR(j.str("site").c_str(), "lab");
    CHECK_STR(j.str("group").c_str(), "bench");
    CHECK_STR(j.str("mode").c_str(), "ACTIVE");
    CHECK_STR(j.str("wifi").c_str(), "down");
    CHECK(cJSON_GetArraySize(j.get("set_fields")) == 14);
    CHECK(j.get("pass") == nullptr && j.get("mqtt_pass") == nullptr && j.get("mcp_token") == nullptr);
}

TEST(prov_v1_hello_gets_crc_reply_v2_get_echoes_id) {
    ProvRig rig;
    rig.send("{\"ionity\":\"prov\",\"op\":\"hello\"}", false);  // portal's hello: prov/1 envelope
    Json a(rig.last());
    CHECK(a.j != nullptr);
    CHECK(a.get("id") == nullptr);
    rig.send("{\"ionity\":\"prov\",\"op\":\"get\",\"v\":2,\"id\":4294967295}");
    Json b(rig.last());
    CHECK_EQ(b.num("id"), 4294967295.0);
    CHECK_STR(b.str("op").c_str(), "get");
}

TEST(prov_crc_mismatch_is_answered_and_not_executed) {
    ProvRig rig;
    const char* bad = "{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":3,\"ssid\":\"X\"} *00000000";
    rig.h.onLine(bad, strlen(bad));
    Json j(rig.last());
    CHECK(j.is("ok", false));
    CHECK_STR(j.str("op").c_str(), "error");
    CHECK_STR(j.str("error").c_str(), "crc mismatch");
    CHECK_STR(rig.kv.str("wifi_ssid").c_str(), "<missing>");
}

TEST(prov_set_validates_then_writes_once) {
    ProvRig rig;
    const int commitsBefore = rig.kv.commits;
    rig.send(
        "{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":9,\"ssid\":\"IONITY-LAB-IOT\",\"pass\":\"lab-pass-123\","
        "\"host_url\":\"mqtt://192.168.0.3:1883\",\"device_uuid\":\"0192F1C4-7D2A-7B3E-9A10-4C5D6E7F8A9B\","
        "\"role\":\"node\",\"site\":\"office\",\"label\":\"Riser GF\",\"mcp_token\":\"tok_0123456789abcdef\"}");
    Json j(rig.last());
    CHECK(j.is("ok", true));
    CHECK_EQ(j.num("id"), 9);
    CHECK(j.is("reboot_required", true));
    CHECK_EQ(cJSON_GetArraySize(j.get("changed")), 8);
    CHECK(j.is("has_pass", true));
    CHECK(j.is("has_mcp_token", true));
    CHECK_STR(j.str("device_uuid").c_str(), "0192f1c4-7d2a-7b3e-9a10-4c5d6e7f8a9b");  // canonical
    CHECK_STR(rig.kv.str("wifi_ssid").c_str(), "IONITY-LAB-IOT");
    CHECK_STR(rig.kv.str("wifi_pass").c_str(), "lab-pass-123");
    CHECK_STR(rig.kv.str("host_url").c_str(), "mqtt://192.168.0.3:1883");
    CHECK_STR(rig.kv.str("server").c_str(), "192.168.0.3");  // fw 2.x keys kept in step
    CHECK_EQ(rig.kv.num("mqtt_port"), 1883);
    CHECK_STR(rig.kv.str("dev_uuid").c_str(), "0192f1c4-7d2a-7b3e-9a10-4c5d6e7f8a9b");
    CHECK_EQ(rig.kv.commits - commitsBefore, 1);  // one commit for the whole set
    for (const std::string& line : rig.out) {
        CHECK(line.find("lab-pass-123") == std::string::npos);
        CHECK(line.find("tok_0123456789abcdef") == std::string::npos);
    }
}

TEST(prov_set_bad_field_writes_nothing) {
    ProvRig rig;
    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":1,\"ssid\":\"Good\",\"site\":\"Bad Site\"}");
    Json j(rig.last());
    CHECK(j.is("ok", false));
    CHECK_STR(j.str("field").c_str(), "site");
    CHECK_STR(rig.kv.str("wifi_ssid").c_str(), "<missing>");  // validate-then-write: ssid not written either

    struct {
        const char* body;
        const char* field;
    } bad[] = {
        {"\"ssid\":\"\"", "ssid"},
        {"\"ssid\":\"012345678901234567890123456789012\"", "ssid"},
        {"\"pass\":\"short\"", "pass"},
        {"\"pass\":\"zz11111111111111111111111111111111111111111111111111111111111111\"", "pass"},
        {"\"host_url\":\"wss://gw.ionity.lan/ewp\"", "host_url"},
        {"\"host_url\":\"mqtt://user:pw@host\"", "host_url"},
        {"\"host_url\":\"mqtt://host:99999\"", "host_url"},
        {"\"device_uuid\":\"not-a-uuid\"", "device_uuid"},
        {"\"ca_pem\":\"hello\"", "ca_pem"},
        {"\"mcp_token\":\"short\"", "mcp_token"},
        {"\"role\":\"gateway\"", "role"},
        {"\"label\":\"line\\nbreak\"", "label"},
        {"\"ssid\":42", "ssid"},
        {"\"wifi_mode\":\"ap\"", "wifi_mode"},  // prov/2: unknown field
        {"\"server\":\"10.0.0.1\"", "server"},   // prov/1 field is not in set_fields
    };
    int id = 2;
    for (const auto& b : bad) {
        rig.send(std::string("{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":") + std::to_string(id++) + "," + b.body + "}");
        Json r(rig.last());
        CHECK(r.is("ok", false));
        CHECK_STR(r.str("field").c_str(), b.field);
    }
    CHECK_EQ(rig.kv.data.count("wifi_ssid"), 0u);
}

TEST(prov_v1_set_is_lenient_and_maps_server_to_host_url) {
    ProvRig rig;
    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"ssid\":\"Guest\",\"server\":\"fleet.ionity.lan\",\"mqtt_port\":1884,"
             "\"pins\":{\"pwm0\":5,\"relay0\":7},\"wifi_mode\":\"ignored\"}",
             false);
    Json j(rig.last());
    CHECK(j.is("ok", true));
    CHECK_STR(rig.kv.str("host_url").c_str(), "mqtt://fleet.ionity.lan:1884");
    CHECK_STR(rig.kv.str("server").c_str(), "fleet.ionity.lan");
    CHECK_EQ(rig.kv.num("mqtt_port"), 1884);
    CHECK_EQ(rig.kv.num("pin_pwm0"), 5);
    CHECK_EQ(rig.kv.num("pin_relay0"), 7);
    CHECK_EQ(rig.kv.num("pin_pwm1"), -1);
    CHECK_STR(j.str("host_url").c_str(), "mqtt://fleet.ionity.lan:1884");
    CHECK_STR(j.str("server").c_str(), "fleet.ionity.lan");  // fw 2.x tools see their fields

    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"mqtt_port\":70000}", false);
    Json k(rig.last());
    CHECK(k.is("ok", false));
    CHECK_STR(k.str("field").c_str(), "mqtt_port");
}

TEST(prov_ca_pem_set_and_clear) {
    ProvRig rig;
    const std::string pem =
        "-----BEGIN CERTIFICATE-----\\nMIIBszCCAVmgAwIBAgIUQ0lPTklUWS1URVNULUNBMAoGCCqGSM49BAMCMBQxEjAQ\\n"
        "-----END CERTIFICATE-----\\n";
    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":1,\"ca_pem\":\"" + pem + "\"}");
    Json j(rig.last());
    CHECK(j.is("ok", true));
    CHECK(j.is("has_ca", true));
    char buf[4000];
    CHECK(rig.cfg.readCa(buf, sizeof buf));
    CHECK(strncmp(buf, "-----BEGIN CERTIFICATE-----\nMIIB", 32) == 0);
    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":2,\"ca_pem\":\"\"}");
    Json k(rig.last());
    CHECK(k.is("has_ca", false));
    CHECK_EQ(rig.kv.data.count("ca_pem"), 0u);
}

TEST(prov_fw2_migration_on_first_boot) {
    ProvRig rig(false);
    // What a fw 2.1.1 board leaves in NVS (Arduino Preferences types).
    rig.kv.setStr("wifi_ssid", "IONITY-LAB-IOT");
    rig.kv.setStr("wifi_pass", "lab-pass-123");
    rig.kv.setStr("server", "192.168.0.3");
    rig.kv.setU16("mqtt_port", 1884);
    rig.kv.setStr("site", "Head Office");  // fw 2.x never validated this
    rig.kv.setU8("mode", 3);               // went to sleep
    rig.kv.setU8("seeded", 1);
    rig.kv.setStr("device_id", "esp32-98a316e5d18c");
    prov::LoadReport rep;
    CHECK(rig.cfg.load(rep));
    CHECK(rep.migratedHostUrl);
    CHECK(rep.siteSanitized);
    CHECK(rep.modeReset);
    prov::NodeConfig c;
    rig.cfg.snapshot(c);
    CHECK_STR(c.hostUrl, "mqtt://192.168.0.3:1884");
    CHECK_STR(rig.kv.str("host_url").c_str(), "mqtt://192.168.0.3:1884");  // persisted: migrates once
    CHECK_STR(c.site, "head-office");
    CHECK_STR(c.ssid, "IONITY-LAB-IOT");
    CHECK_EQ(c.mode, 0);
    CHECK_EQ(c.bootCount, 1u);
    CHECK(c.deviceUuid[0] == '\0');  // stays empty until provisioned

    prov::LoadReport rep2;
    CHECK(rig.cfg.load(rep2));  // second boot: nothing to migrate
    CHECK(!rep2.migratedHostUrl);
    rig.cfg.snapshot(c);
    CHECK_EQ(c.bootCount, 2u);
}

TEST(prov_fw2_empty_server_means_mdns) {
    ProvRig rig(false);
    rig.kv.setStr("server", "");
    prov::LoadReport rep;
    rig.cfg.load(rep);
    prov::NodeConfig c;
    rig.cfg.snapshot(c);
    CHECK_STR(c.hostUrl, "");
    CHECK_STR(rig.kv.str("host_url").c_str(), "");
}

TEST(prov_test_scan_and_busy) {
    ProvRig rig;
    rig.send("{\"ionity\":\"prov\",\"op\":\"test\",\"v\":2,\"id\":1}");
    Json a(rig.last());
    CHECK_STR(a.str("error").c_str(), "no ssid provisioned");

    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":2,\"ssid\":\"IONITY-LAB-IOT\",\"pass\":\"lab-pass-123\"}");
    rig.send("{\"ionity\":\"prov\",\"op\":\"test\",\"v\":2,\"id\":3,\"timeout_ms\":99999}");
    Json t(rig.last());
    CHECK(t.is("ok", true));
    CHECK_EQ(t.num("id"), 3);
    CHECK_EQ(rig.be.lastTestTimeout, 30000u);  // clamped
    const cJSON* wifi = t.get("wifi");
    CHECK(cJSON_IsTrue(cJSON_GetObjectItem(wifi, "ok")));
    CHECK_STR(cJSON_GetObjectItem(wifi, "ip")->valuestring, "192.168.0.42");
    CHECK(cJSON_IsTrue(cJSON_GetObjectItem(t.get("broker"), "ok")));
    CHECK_STR(cJSON_GetObjectItem(t.get("board"), "op")->valuestring, "get");

    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":4,\"pass\":\"wrong-pass-1\"}");
    rig.send("{\"ionity\":\"prov\",\"op\":\"test\",\"v\":2,\"id\":5}");
    Json f(rig.last());
    CHECK(f.is("ok", false));
    CHECK(cJSON_IsFalse(cJSON_GetObjectItem(f.get("wifi"), "ok")));
    CHECK(strstr(cJSON_GetObjectItem(f.get("wifi"), "error")->valuestring, "password") != nullptr);

    rig.be.includeOddSsid = true;
    rig.send("{\"ionity\":\"prov\",\"op\":\"scan\",\"v\":2,\"id\":6}");
    Json s(rig.last());
    CHECK(s.is("ok", true));
    CHECK_EQ(cJSON_GetArraySize(s.get("networks")), 4);
    const cJSON* odd = cJSON_GetArrayItem(s.get("networks"), 3);
    CHECK_STR(cJSON_GetObjectItem(odd, "ssid")->valuestring, "caf\xef\xbf\xbd");  // non-UTF-8 byte -> U+FFFD

    rig.be.radioBusy = true;
    rig.send("{\"ionity\":\"prov\",\"op\":\"scan\",\"v\":2,\"id\":7}");
    Json b(rig.last());
    CHECK(b.is("ok", false));
    CHECK(!rig.h.busy());
}

TEST(prov_standalone_test_reports_no_broker) {
    ProvRig rig;
    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":1,\"ssid\":\"Guest\",\"pass\":\"\",\"role\":\"standalone\"}");
    rig.send("{\"ionity\":\"prov\",\"op\":\"test\",\"v\":2,\"id\":2}");
    Json t(rig.last());
    CHECK(t.is("ok", true));
    CHECK(cJSON_GetObjectItem(t.get("broker"), "ok") == nullptr);  // undefined in normalizeTest
}

TEST(prov_v1_test_reply_is_flat) {
    ProvRig rig;
    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"ssid\":\"Guest\",\"pass\":\"\"}", false);
    rig.send("{\"ionity\":\"prov\",\"op\":\"test\"}", false);
    Json t(rig.last());
    CHECK(t.is("ok", true));
    CHECK(t.is("mqtt", true));
    CHECK(t.get("wifi") != nullptr && cJSON_IsString(t.get("wifi")));  // flat info, "connected"
}

TEST(prov_reboot_and_factory_reset) {
    ProvRig rig;
    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":1,\"ssid\":\"Guest\"}");
    rig.send("{\"ionity\":\"prov\",\"op\":\"reboot\",\"v\":2,\"id\":2}");
    Json r(rig.last());
    CHECK(r.is("ok", true));
    CHECK_EQ(rig.be.restarts, 1);
    rig.send("{\"ionity\":\"prov\",\"op\":\"factory_reset\",\"v\":2,\"id\":3}");
    Json f(rig.last());
    CHECK(f.is("ok", true));
    CHECK_EQ(rig.be.restarts, 2);
    CHECK_EQ(rig.be.wifiResets, 1);
    CHECK(rig.kv.data.empty());
    rig.send("{\"ionity\":\"prov\",\"op\":\"get\",\"v\":2,\"id\":4}");
    Json g(rig.last());
    CHECK(g.is("provisioned", false));
}

TEST(prov_rejects_hostile_lines) {
    ProvRig rig;
    rig.send("{\"ionity\":\"prov\",\"op\":\"get\"} trailing", false);
    CHECK(rig.out.empty());  // not a request: ignored like log noise
    rig.send("{\"ionity\":\"other\",\"op\":\"get\"}", false);
    CHECK(rig.out.empty());
    rig.send("not json at all", false);
    CHECK(rig.out.empty());

    std::string many = "{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"a\":[";
    for (int i = 0; i < 200; ++i) many += "1,";
    many += "1]}";
    rig.send(many);
    Json j(rig.last());
    CHECK_STR(j.str("error").c_str(), "request too complex");

    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":8,\"ssid\":\"a\\u0000b\"}");
    Json n(rig.last());
    CHECK_STR(n.str("error").c_str(), "control characters are not allowed");

    rig.send("{\"ionity\":\"prov\",\"op\":\"jump\",\"v\":2,\"id\":9}");
    Json u(rig.last());
    CHECK_STR(u.str("error").c_str(), "unknown op");
    CHECK_STR(u.str("op").c_str(), "jump");
}

TEST(prov_storage_failure_is_reported) {
    ProvRig rig;
    rig.kv.failWrites = true;
    rig.send("{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":1,\"ssid\":\"Guest\"}");
    Json j(rig.last());
    CHECK(j.is("ok", false));
    prov::NodeConfig c;
    rig.cfg.snapshot(c);
    CHECK_STR(c.ssid, "");  // RAM copy not updated either
}

/* ---- line assembly ---------------------------------------------------------- */

TEST(prov_line_assembler) {
    prov::LineAssembler<16> la;
    std::vector<std::string> lines;
    int overlong = 0;
    auto onLine = [&](const char* l, size_t n) { lines.emplace_back(l, n); };
    auto onOver = [&] { ++overlong; };
    const char* a = "{\"a\":1}\r\n{\"b\"";
    la.feed(reinterpret_cast<const uint8_t*>(a), strlen(a), onLine, onOver);
    const char* b = ":2}\n";
    la.feed(reinterpret_cast<const uint8_t*>(b), strlen(b), onLine, onOver);
    CHECK_EQ(lines.size(), 2u);
    CHECK_STR(lines[0].c_str(), "{\"a\":1}");
    CHECK_STR(lines[1].c_str(), "{\"b\":2}");
    const char* exact = "0123456789abcdef\n";  // exactly 16: accepted
    la.feed(reinterpret_cast<const uint8_t*>(exact), strlen(exact), onLine, onOver);
    CHECK_EQ(lines.size(), 3u);
    const char* tooLong = "0123456789abcdefX tail of the long line\n{\"c\":3}\n";
    la.feed(reinterpret_cast<const uint8_t*>(tooLong), strlen(tooLong), onLine, onOver);
    CHECK_EQ(overlong, 1);
    CHECK_EQ(lines.size(), 4u);  // the long line is dropped whole, the next one survives
    CHECK_STR(lines[3].c_str(), "{\"c\":3}");
}

TEST(prov_scrubber) {
    prov::Scrubber s;
    s.add("lab-pass-123");
    s.add("tok_0123456789abcdef");
    const std::string shortSecret = "abc";  // runtime string: GCC cannot fold the unreachable copy
    s.add(shortSecret.c_str());             // too short to scrub safely: ignored
    char line[128];
    strcpy(line, "W wifi: join with lab-pass-123 failed; token tok_0123456789abcdef; abc stays");
    const size_t n = s.scrub(line, strlen(line));
    line[n] = '\0';
    CHECK_STR(line, "W wifi: join with *** failed; token ***; abc stays");
    CHECK_EQ(s.count(), 2u);
}

/* ---- validators (portal parity) -------------------------------------------- */

TEST(prov_host_url_canonical_forms) {
    struct {
        const char* in;
        const char* href;
    } ok[] = {
        {"192.168.0.3", "mqtt://192.168.0.3:1883"},
        {"mqtts://Fleet.Ionity.LAN", "mqtts://fleet.ionity.lan:8883"},
        {"wss://gw.ionity.lan/ewp", "wss://gw.ionity.lan:443/ewp"},
        {"http://192.168.0.3", "http://192.168.0.3:8099"},
        {"  mqtt://[fd00::1]:1884  ", "mqtt://[fd00::1]:1884"},
        {"mqtt://host:1883/", "mqtt://host:1883"},
    };
    for (const auto& c : ok) {
        prov::HostUrl u;
        const char* err = nullptr;
        CHECK(prov::parseHostUrl(c.in, strlen(c.in), u, &err));
        CHECK_STR(u.href, c.href);
    }
    const char* bad[] = {"",
                         "ftp://host",
                         "mqtt://user:pw@host",
                         "mqtt://host:0",
                         "mqtt://host:65536",
                         "mqtt://host/path",
                         "mqtt://host?x=1",
                         "mqtt://-bad.host",
                         "mqtt://256.1.1.1",
                         "mqtt://01.1.1.1",
                         "mqtt://[zz::1]",
                         "mqtt://ho st",
                         "mqtt://host:12a"};
    for (const char* b : bad) {
        prov::HostUrl u;
        const char* err = nullptr;
        if (prov::parseHostUrl(b, strlen(b), u, &err)) ::check::fail(__FILE__, __LINE__, b);
        CHECK(err != nullptr);
    }
}

TEST(prov_field_validators) {
    const char* err = nullptr;
    CHECK(prov::validWifiPass("", 0, &err));
    CHECK(prov::validWifiPass("12345678", 8, &err));
    CHECK(!prov::validWifiPass("1234567", 7, &err));
    const char* hex64 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789ABCDEF";
    CHECK(prov::validWifiPass(hex64, 64, &err));
    CHECK(prov::validTopicLevel("lab-2", 5));
    CHECK(!prov::validTopicLevel("-lab", 4));
    CHECK(!prov::validTopicLevel("Lab", 3));
    CHECK(!prov::validTopicLevel("a/b", 3));
    CHECK(!prov::validTopicLevel("a+", 2));
    CHECK(prov::validToken("abcdefghijklmnop", 16));
    CHECK(!prov::validToken("abcdefghijklmno", 15));
    CHECK(!prov::validToken("abcdefghijklmnop!", 17));
    char u[37];
    CHECK(prov::normalizeUuid("{0192F1C4-7D2A-7B3E-9A10-4C5D6E7F8A9B}", 38, u));
    CHECK_STR(u, "0192f1c4-7d2a-7b3e-9a10-4c5d6e7f8a9b");
    CHECK(!prov::normalizeUuid("0192f1c4-7d2a-0b3e-9a10-4c5d6e7f8a9b", 36, u));  // version 0
    CHECK(!prov::normalizeUuid("0192f1c4-7d2a-7b3e-1a10-4c5d6e7f8a9b", 36, u));  // variant
    CHECK(prov::hasControlChars("a\x7f", 2));
    CHECK(prov::hasControlChars("a\xc2\x85", 3));  // U+0085 NEL
    CHECK(!prov::hasControlChars("caf\xc3\xa9", 5));
    char site[33];
    prov::sanitizeTopicLevel("--Head Office #1", site, sizeof site, "lab");
    CHECK_STR(site, "head-office--1");
    prov::sanitizeTopicLevel("###", site, sizeof site, "lab");
    CHECK_STR(site, "lab");
}
