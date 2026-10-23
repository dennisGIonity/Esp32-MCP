/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/test_tools.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * TELEMETRY / HELLO / STATUS encoders and the three Phase 3 tools, end to end
 * through MCPServer (REQ in, RES/ERR out).
 */
#include <string.h>

#include <atomic>
#include <string>

#include "check.h"
#include "fakes.h"
#include "frames.h"
#include "mcp/MCPServer.h"
#include "tools/Actuators.h"
#include "tools/EdgeTools.h"
#include "tools/SystemTools.h"

using namespace tf;

namespace {

struct ToolRig {
    tools::Identity id;
    fakes::Source src;
    fakes::Led led;
    mcp::Uplink up;
    mcp::MCPServer srv;
    tools::EdgeTools edge;
    bool link = true;

    explicit ToolRig(bool withLed = true) {
        fakes::identity(id);
        CHECK(up.begin(8, 2048, false));
        tools::EdgeToolsConfig cfg;
        cfg.identity = &id;
        cfg.source = &src;
        cfg.indicator = withLed ? &led : nullptr;
        cfg.uplink = &up;
        cfg.linkUp = [](void* u) { return static_cast<ToolRig*>(u)->link; };
        cfg.linkUser = this;
        CHECK(edge.begin(cfg, srv));
        mcp::ServerConfig sc;
        sc.workerCore = -1;
        CHECK(srv.begin(sc, up));
    }
    ~ToolRig() { srv.shutdown(); }

    /** Send a REQ and return its RES/ERR. */
    Out call(uint32_t rid, uint8_t tool, const Bytes& body = {0xA0}) {
        const Bytes f = req(rid, tool, 0, 2000, body);
        srv.onDownlink(f.data(), f.size(), ewp::Topic::Unicast);
        Out o;
        CHECK(pop(up, o));
        return o;
    }
};

bool resultOf(const Out& o, const uint8_t*& r, size_t& rn) {
    return o.h.type == EWP_MSG_RES && getRaw(o.body(), o.bodyLen(), EWP_K_RESULT_BODY_RESULT, r, rn);
}

}  // namespace

/* ---- encoders -------------------------------------------------------------- */

TEST(telemetry_all_sections) {
    fakes::Source src;
    tools::TelemetrySnapshot s;
    src.collect(s, 0xFF);
    s.seq = 42;
    uint8_t buf[512];
    ewp::CborWriter w(buf, sizeof buf);
    tools::encodeTelemetryBody(w, s, tools::kSupportedSections);
    CHECK(w.ok());
    CHECK(wellFormed(buf, w.size()));
    uint64_t v = 0;
    CHECK(getUint(buf, w.size(), EWP_K_TELEMETRY_BODY_SEQ, v) && v == 42);
    CHECK(getUint(buf, w.size(), EWP_K_TELEMETRY_BODY_UPTIME_S, v) && v == 3600);
    CHECK(getUint(buf, w.size(), EWP_K_TELEMETRY_BODY_UNIX_S, v) && v == 1791763200);
    CHECK(getUint(buf, w.size(), EWP_K_TELEMETRY_BODY_MODE, v) && v == EWP_MODE_ACTIVE);
    const uint8_t* h = nullptr;
    size_t hn = 0;
    CHECK(getRaw(buf, w.size(), EWP_K_TELEMETRY_BODY_HEAP, h, hn));
    CHECK(getUint(h, hn, EWP_K_HEAP_STATS_INTERNAL_LARGEST, v) && v == 110592);
    CHECK(getUint(h, hn, EWP_K_HEAP_STATS_PSRAM_FREE, v) && v == 8302336);
    const uint8_t* n = nullptr;
    size_t nn = 0;
    CHECK(getRaw(buf, w.size(), EWP_K_TELEMETRY_BODY_NET, n, nn));
    int64_t rssi = 0;
    CHECK(getInt(n, nn, EWP_K_NET_STATS_RSSI_DBM, rssi) && rssi == -61);
    for (uint64_t k : {EWP_K_TELEMETRY_BODY_CPU, EWP_K_TELEMETRY_BODY_TASKS, EWP_K_TELEMETRY_BODY_SENSORS,
                       EWP_K_TELEMETRY_BODY_ACTUATORS}) {
        CHECK(hasKey(buf, w.size(), k));
    }
    CHECK(!hasKey(buf, w.size(), EWP_K_TELEMETRY_BODY_INFERENCE));
    CHECK(!hasKey(buf, w.size(), EWP_K_TELEMETRY_BODY_EXT));
    CHECK(w.size() < 300);  // fits a QoS 0 frame comfortably
}

TEST(telemetry_omits_unknowns_instead_of_zeros) {
    tools::TelemetrySnapshot s;  // nothing collected: no PSRAM, not associated, CPU not primed
    uint8_t buf[256];
    ewp::CborWriter w(buf, sizeof buf);
    tools::encodeTelemetryBody(w, s, tools::kDefaultSections);
    CHECK(w.ok());
    CHECK(!hasKey(buf, w.size(), EWP_K_TELEMETRY_BODY_CPU));    // no window yet
    CHECK(!hasKey(buf, w.size(), EWP_K_TELEMETRY_BODY_TASKS));  // not in the default set
    const uint8_t* h = nullptr;
    size_t hn = 0;
    CHECK(getRaw(buf, w.size(), EWP_K_TELEMETRY_BODY_HEAP, h, hn));
    CHECK(!hasKey(h, hn, EWP_K_HEAP_STATS_PSRAM_FREE));  // ESP32-C6: absent, not 0
    const uint8_t* n = nullptr;
    size_t nn = 0;
    CHECK(getRaw(buf, w.size(), EWP_K_TELEMETRY_BODY_NET, n, nn));
    CHECK(!hasKey(n, nn, EWP_K_NET_STATS_RSSI_DBM));
}

TEST(hello_body_shape) {
    tools::Identity id;
    fakes::identity(id);
    mcp::MCPServer srv;
    fakes::Source src;
    mcp::Uplink up;
    CHECK(up.begin(2, 2048, false));
    tools::EdgeTools edge;
    tools::EdgeToolsConfig cfg;
    cfg.identity = &id;
    cfg.source = &src;
    cfg.uplink = &up;
    CHECK(edge.begin(cfg, srv));  // no LED: identify not offered
    uint8_t buf[1024];
    ewp::CborWriter w(buf, sizeof buf);
    tools::encodeHelloBody(w, id, EWP_MODE_ACTIVE, srv);
    CHECK(w.ok());
    CHECK(wellFormed(buf, w.size()));
    std::string s;
    CHECK(getText(buf, w.size(), EWP_K_HELLO_BODY_HWID, s));
    CHECK_STR(s.c_str(), "esp32-98a316e5d18c");
    const uint8_t* u = nullptr;
    size_t un = 0;
    CHECK(getRaw(buf, w.size(), EWP_K_HELLO_BODY_UUID, u, un));
    CHECK_EQ(un, 17u);  // 0x50 + 16 bytes
    const uint8_t* tl = nullptr;
    size_t tn = 0;
    CHECK(getRaw(buf, w.size(), EWP_K_HELLO_BODY_TOOLS, tl, tn));
    CHECK_HEX(tl, tn, "82 83 02 01 09 83 01 01 09");  // [[2,1,RO|IDEM],[1,1,RO|IDEM]]
    CHECK(getText(buf, w.size(), EWP_K_HELLO_BODY_LABEL, s));
    CHECK_STR(s.c_str(), "riser GF");
    uint64_t v = 0;
    CHECK(getUint(buf, w.size(), EWP_K_HELLO_BODY_BOOT_COUNT, v) && v == 7);
    CHECK(getUint(buf, w.size(), EWP_K_HELLO_BODY_RESET_REASON, v) && v == EWP_RESET_POWER_ON);
    const uint8_t* tk = nullptr;
    size_t tkn = 0;
    CHECK(getRaw(buf, w.size(), EWP_K_HELLO_BODY_TASKS, tk, tkn));
    ewp::CborReader r(tk, tkn);
    uint32_t count = 0;
    CHECK(r.readArray(count) && count == 3);
    uint32_t four = 0;
    CHECK(r.readArray(four) && four == 4);
    CHECK(w.size() < 400);
}

TEST(status_body_and_device_frame) {
    uint8_t buf[64];
    const uint8_t ip[4] = {192, 168, 0, 42};
    const size_t len = tools::buildDeviceFrame(buf, sizeof buf, EWP_MSG_STATUS, 3, [&](ewp::CborWriter& w) {
        tools::encodeStatusBody(w, EWP_STATE_ONLINE, EWP_MODE_ACTIVE, ip, 4, 0);
    });
    CHECK(len > EWP_HEADER_LEN);
    CHECK_HEX(buf, len, "e5 01 21 00 03 00 00 00 00 00 00 00 0b 00 00 00 a3 01 01 02 00 04 44 c0 a8 00 2a");
    // Last Will: OFFLINE, nothing else
    const size_t lw = tools::buildDeviceFrame(buf, sizeof buf, EWP_MSG_STATUS, 0, [&](ewp::CborWriter& w) {
        tools::encodeStatusBody(w, EWP_STATE_OFFLINE, -1, nullptr, 0, 0);
    });
    CHECK_HEX(buf, lw, "e5 01 21 00 00 00 00 00 00 00 00 00 03 00 00 00 a1 01 00");
}

TEST(uuid_and_hwid_helpers) {
    uint8_t u[16];
    CHECK(tools::parseUuid("0192f1c4-7d2a-7b3e-9a10-4c5d6e7f8a9b", u));
    CHECK_HEX(u, 16, "01 92 f1 c4 7d 2a 7b 3e 9a 10 4c 5d 6e 7f 8a 9b");
    CHECK(!tools::parseUuid("0192F1C4-7d2a-7b3e-9a10-4c5d6e7f8a9b", u));  // canonical = lowercase
    CHECK(!tools::parseUuid("0192f1c4-7d2a-7b3e-9a10-4c5d6e7f8a9", u));
    CHECK(!tools::parseUuid("0192f1c4x7d2a-7b3e-9a10-4c5d6e7f8a9b", u));
    CHECK(!tools::parseUuid("", u));
    char h[24];
    const uint8_t mac[6] = {0, 1, 2, 0xab, 0xcd, 0xef};
    tools::formatHwid(mac, h, sizeof h);
    CHECK_STR(h, "esp32-000102abcdef");
}

/* ---- tools through MCPServer --------------------------------------------- */

TEST(tool_read_telemetry_default_and_sections) {
    ToolRig rig;
    Out o = rig.call(1, EWP_TOOL_READ_TELEMETRY);  // {} -> default sections
    const uint8_t* r = nullptr;
    size_t rn = 0;
    CHECK(resultOf(o, r, rn));
    CHECK(hasKey(r, rn, EWP_K_TELEMETRY_BODY_HEAP));
    CHECK(hasKey(r, rn, EWP_K_TELEMETRY_BODY_NET));
    CHECK(hasKey(r, rn, EWP_K_TELEMETRY_BODY_CPU));
    CHECK(hasKey(r, rn, EWP_K_TELEMETRY_BODY_ACTUATORS));
    CHECK(!hasKey(r, rn, EWP_K_TELEMETRY_BODY_TASKS));

    o = rig.call(2, EWP_TOOL_READ_TELEMETRY, {0xA1, 0x01, 0x18, 0x18});  // sections = TASKS|SENSORS
    CHECK(resultOf(o, r, rn));
    CHECK(hasKey(r, rn, EWP_K_TELEMETRY_BODY_TASKS));
    CHECK(hasKey(r, rn, EWP_K_TELEMETRY_BODY_SENSORS));
    CHECK(!hasKey(r, rn, EWP_K_TELEMETRY_BODY_HEAP));

    o = rig.call(3, EWP_TOOL_READ_TELEMETRY, {0xA1, 0x01, 0x18, 0xC0});  // INFERENCE|EXT only: none supported
    CHECK(resultOf(o, r, rn));
    CHECK(hasKey(r, rn, EWP_K_TELEMETRY_BODY_SEQ));
    CHECK(!hasKey(r, rn, EWP_K_TELEMETRY_BODY_INFERENCE));
}

TEST(tool_read_telemetry_rejects_bad_args) {
    ToolRig rig;
    struct {
        Bytes body;
        int argKey;
    } bad[] = {
        {{0xA1, 0x01, 0x19, 0x01, 0x2C}, 1},   // sections = 300
        {{0xA1, 0x01, 0x20}, 1},               // sections = -1
        {{0xA1, 0x01, 0x61, 0x78}, 1},         // sections = "x"
        {{0xA2, 0x01, 0x01, 0x01, 0x02}, 1},   // duplicate key 1
        {{0xA1, 0x61, 0x31, 0x01}, -1},        // text key "1" (a JS object, not a Map)
        {{0x81, 0x01}, -1},                    // array instead of map
        {{0xA0, 0x00}, -1},                    // trailing byte
        {{0xA1, 0x01}, -1},                    // truncated
    };
    uint32_t rid = 10;
    for (const auto& b : bad) {
        Out o = rig.call(rid++, EWP_TOOL_READ_TELEMETRY, b.body);
        CHECK_EQ(o.h.type, EWP_MSG_ERR);
        CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);
        uint64_t k = 0;
        const bool has = getUint(o.body(), o.bodyLen(), EWP_K_ERROR_BODY_ARG_KEY, k);
        if (b.argKey >= 0) {
            CHECK(has && static_cast<int>(k) == b.argKey);
        } else {
            CHECK(!has);
        }
    }
    // unknown keys are skipped (forward compatible)
    Out o = rig.call(rid++, EWP_TOOL_READ_TELEMETRY, {0xA1, 0x09, 0x82, 0x01, 0x02});
    CHECK_EQ(o.h.type, EWP_MSG_RES);
}

TEST(tool_get_device_info_matches_hello) {
    ToolRig rig;
    Out o = rig.call(5, EWP_TOOL_GET_DEVICE_INFO);
    const uint8_t* r = nullptr;
    size_t rn = 0;
    CHECK(resultOf(o, r, rn));
    uint8_t buf[1024];
    ewp::CborWriter w(buf, sizeof buf);
    rig.edge.encodeHello(w);
    CHECK(w.ok());
    CHECK_EQ(rn, w.size());
    CHECK(rn == w.size() && memcmp(r, buf, rn) == 0);  // byte-identical to the retained HELLO
}

TEST(tool_identify) {
    ToolRig rig;
    Out o = rig.call(6, EWP_TOOL_IDENTIFY, {0xA1, 0x01, 0x19, 0x07, 0xD0});  // 2000 ms
    CHECK_EQ(o.h.type, EWP_MSG_RES);
    const uint8_t* r = nullptr;
    size_t rn = 0;
    CHECK(resultOf(o, r, rn));
    CHECK_HEX(r, rn, "a0");
    CHECK_EQ(rig.led.lastMs.load(), 2000u);
    o = rig.call(7, EWP_TOOL_IDENTIFY);
    CHECK_EQ(rig.led.lastMs.load(), 1200u);  // default
    o = rig.call(8, EWP_TOOL_IDENTIFY, {0xA1, 0x01, 0x00});
    CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);
    o = rig.call(9, EWP_TOOL_IDENTIFY, {0xA1, 0x01, 0x19, 0x27, 0x11});  // 10001
    CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);
    CHECK_EQ(rig.led.blinks.load(), 2);
}

TEST(tool_identify_absent_without_led) {
    ToolRig rig(false);
    Out o = rig.call(6, EWP_TOOL_IDENTIFY);
    CHECK_EQ(o.h.type, EWP_MSG_ERR);
    CHECK_EQ(o.h.status, EWP_ST_UNKNOWN_TOOL);
}

TEST(tool_periodic_telemetry_frame) {
    ToolRig rig;
    CHECK(rig.srv.postTick());
    Out o;
    CHECK(pop(rig.up, o));
    CHECK_EQ(o.h.type, EWP_MSG_TELEMETRY);
    CHECK_EQ(o.channel, static_cast<uint8_t>(ewp::Channel::Telemetry));
    CHECK_EQ(o.h.rid, 1u);
    uint64_t seq = 0;
    CHECK(getUint(o.body(), o.bodyLen(), EWP_K_TELEMETRY_BODY_SEQ, seq) && seq == 1);
    CHECK(hasKey(o.body(), o.bodyLen(), EWP_K_TELEMETRY_BODY_TASKS));  // periodic carries the full set
    for (int i = 0; i < 100 && !rig.srv.postTick(); ++i) os::delayMs(1);
    CHECK(pop(rig.up, o));
    CHECK(getUint(o.body(), o.bodyLen(), EWP_K_TELEMETRY_BODY_SEQ, seq) && seq == 2);
    // on-demand reads report the current seq without advancing it
    Out r = rig.call(30, EWP_TOOL_READ_TELEMETRY);
    const uint8_t* rr = nullptr;
    size_t rn = 0;
    CHECK(resultOf(r, rr, rn));
    CHECK(getUint(rr, rn, EWP_K_TELEMETRY_BODY_SEQ, seq) && seq == 2);
    // link down: the tick is skipped, nothing is queued
    rig.link = false;
    for (int i = 0; i < 100 && !rig.srv.postTick(); ++i) os::delayMs(1);
    CHECK(!pop(rig.up, o, 60));
    CHECK_EQ(rig.edge.telemetrySkipped(), 1u);
}

/* ---- actuators + system tools ------------------------------------------------------- */


namespace {

class FakeHw final : public tools::ActuatorHw {
public:
    float pin[3] = {-1, -1, -1};
    bool configure(uint8_t, uint8_t, int gpio, uint8_t) override { return gpio != 13; }  // GPIO 13 "broken"
    float write(uint8_t ch, uint8_t kind, float v) override {
        // 10-bit LEDC quantisation for PWM
        const float q = kind == EWP_ACT_PWM ? static_cast<float>(static_cast<int>(v * 1023.0f + 0.5f)) / 1023.0f : v;
        pin[ch] = q;
        return q;
    }
};

struct SysRig {
    tools::Identity id;
    fakes::Source src;
    mcp::Uplink up;
    mcp::MCPServer srv;
    tools::EdgeTools edge;
    tools::ActuatorBank bank;
    FakeHw hw;
    tools::SystemTools sys;
    std::atomic<uint32_t> restartIn{0};
    int expired = 0;

    SysRig() {
        fakes::identity(id);
        CHECK(bank.addChannel("pwm0", EWP_ACT_PWM, 5, 0.0f, false));
        CHECK(bank.addChannel("pwm1", EWP_ACT_PWM, 6, 0.0f, false));
        CHECK(bank.addChannel("relay0", EWP_ACT_RELAY, 7, 0.0f, true));
        CHECK(bank.begin(hw, [](void* u, uint8_t, float) { ++static_cast<SysRig*>(u)->expired; }, this));
        id.actuators = &bank;
        CHECK(up.begin(8, 2048, false));
        tools::EdgeToolsConfig ec;
        ec.identity = &id;
        ec.source = &src;
        ec.uplink = &up;
        CHECK(edge.begin(ec, srv));
        tools::SystemToolsConfig sc;
        sc.actuators = &bank;
        sc.edge = &edge;
        sc.scheduleRestart = [](void* u, uint32_t ms) { static_cast<SysRig*>(u)->restartIn.store(ms); };
        sc.user = this;
        CHECK(sys.begin(sc, srv));
        mcp::ServerConfig cfg;
        cfg.workerCore = -1;
        CHECK(srv.begin(cfg, up));
    }
    ~SysRig() { srv.shutdown(); }
    Out call(uint32_t rid, uint8_t tool, const Bytes& body) {
        const Bytes f = req(rid, tool, 0, 2000, body);
        srv.onDownlink(f.data(), f.size(), ewp::Topic::Unicast);
        Out o;
        CHECK(pop(up, o));
        return o;
    }
};

Bytes setArgsInt(uint8_t ch, uint64_t v) {
    return Bytes{0xA2, EWP_K_SET_ACTUATOR_ARGS_CHANNEL, ch, EWP_K_SET_ACTUATOR_ARGS_VALUE, static_cast<uint8_t>(v)};
}

}  // namespace

TEST(actuators_set_lease_pulse_and_failsafe) {
    SysRig rig;
    Out o = rig.call(1, EWP_TOOL_SET_ACTUATOR, setArgsInt(0, 1));  // pwm0 = 1 (an int is a valid float)
    CHECK_EQ(o.h.type, EWP_MSG_RES);
    CHECK_EQ(rig.hw.pin[0], 1.0f);
    o = rig.call(2, EWP_TOOL_SET_ACTUATOR, setArgsInt(2, 1));  // relay0 on
    CHECK_EQ(o.h.type, EWP_MSG_RES);
    // relay takes 0/1 only; unknown channel; value > 1
    uint8_t half[] = {0xA2, 0x01, 0x02, 0x02, 0xFA, 0x3F, 0x00, 0x00, 0x00};  // {1: 2, 2: 0.5f}
    o = rig.call(3, EWP_TOOL_SET_ACTUATOR, Bytes(half, half + sizeof half));
    CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);
    o = rig.call(4, EWP_TOOL_SET_ACTUATOR, setArgsInt(9, 1));
    CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);
    o = rig.call(5, EWP_TOOL_SET_ACTUATOR, setArgsInt(0, 2));
    CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);
    o = rig.call(6, EWP_TOOL_SET_ACTUATOR, Bytes{0xA1, 0x01, 0x00});
    CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);  // value missing

    // lease: reverts to safe and reports the expiry
    const int64_t t0 = os::nowUs();
    CHECK_EQ(rig.bank.set(1, 0.5f, 0, 100, false, t0).status, EWP_ST_OK);
    CHECK(rig.hw.pin[1] > 0.49f && rig.hw.pin[1] < 0.51f);
    rig.bank.service(t0 + 50000);
    CHECK(rig.hw.pin[1] > 0.4f);
    rig.bank.service(t0 + 150000);
    CHECK_EQ(rig.hw.pin[1], 0.0f);
    CHECK_EQ(rig.expired, 1);
    // pulse overlays without forgetting the host's value
    CHECK_EQ(rig.bank.set(1, 0.25f, 0, 0, false, t0).status, EWP_ST_OK);
    rig.bank.pulse(1, 100, t0);
    CHECK_EQ(rig.hw.pin[1], 1.0f);
    rig.bank.service(t0 + 200000);
    CHECK(rig.hw.pin[1] > 0.24f && rig.hw.pin[1] < 0.26f);
    // ramp
    CHECK_EQ(rig.bank.set(1, 1.0f, 1000, 0, false, t0).status, EWP_ST_OK);
    rig.bank.service(t0 + 500000);
    CHECK(rig.hw.pin[1] > 0.55f && rig.hw.pin[1] < 0.7f);
    rig.bank.service(t0 + 1100000);
    CHECK_EQ(rig.hw.pin[1], 1.0f);

    float vals[4];
    CHECK_EQ(rig.bank.values(vals, 4), 3u);
    // FAILSAFE locks the outputs
    rig.edge.setMode(EWP_MODE_FAILSAFE);
    o = rig.call(7, EWP_TOOL_SET_ACTUATOR, setArgsInt(0, 0));
    CHECK_EQ(o.h.status, EWP_ST_FAILSAFE_LOCKED);
    rig.bank.allSafe(t0);
    CHECK_EQ(rig.hw.pin[0], 0.0f);
    CHECK_EQ(rig.hw.pin[2], 0.0f);
}

TEST(system_reboot_and_hello_actuators) {
    SysRig rig;
    Out o = rig.call(1, EWP_TOOL_REBOOT, Bytes{0xA0});
    CHECK_EQ(o.h.type, EWP_MSG_RES);
    CHECK_EQ(rig.restartIn.load(), 500u);
    o = rig.call(2, EWP_TOOL_REBOOT, Bytes{0xA1, 0x01, 0x00});
    CHECK_EQ(rig.restartIn.load(), 300u);  // never before the reply can leave
    o = rig.call(3, EWP_TOOL_REBOOT, Bytes{0xA1, 0x01, 0x19, 0x27, 0x11});
    CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);

    uint8_t buf[1024];
    ewp::CborWriter w(buf, sizeof buf);
    rig.edge.encodeHello(w);
    CHECK(w.ok());
    const uint8_t* a = nullptr;
    size_t an = 0;
    CHECK(getRaw(buf, w.size(), EWP_K_HELLO_BODY_ACTUATORS, a, an));
    ewp::CborReader r(a, an);
    uint32_t n = 0;
    CHECK(r.readArray(n) && n == 3);
    std::string name;
    CHECK(getText(a + 1, an - 1, EWP_K_ACTUATOR_DESCRIPTOR_NAME, name));
    CHECK_STR(name.c_str(), "pwm0");
    const uint8_t* tl = nullptr;
    size_t tn = 0;
    CHECK(getRaw(buf, w.size(), EWP_K_HELLO_BODY_TOOLS, tl, tn));
    ewp::CborReader tr(tl, tn);
    CHECK(tr.readArray(n) && n == 4);  // read_telemetry, get_device_info, set_actuator, reboot
}
