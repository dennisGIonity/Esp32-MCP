/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/ewp_dump.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Frames from the firmware encoders, for the TypeScript (cbor-x) interop test.
 *
 *   ./ewp_dump frames            one "<name> <hex>" line per device frame kind
 *   ./ewp_dump req <hex>...      run REQ/CANCEL frames through MCPServer + EdgeTools
 *                                and print every uplink frame ("<hex>" per line)
 *   ./ewp_dump netobs <hex>...   a real NetObserve forwarder on loopback: two lookups
 *                                (one on the watchlist), then "<name> <hex>" for
 *                                flag / batch / hello / telemetry, then the REQs given
 *                                (NetObserve tools, set_actuator) and their replies
 */
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#include "fakes.h"
#include "frames.h"
#include "mcp/MCPServer.h"
#include "netobs_fakes.h"
#include "tools/Actuators.h"
#include "tools/EdgeTools.h"
#include "tools/Identity.h"
#include "tools/SystemTools.h"

#include <unistd.h>

namespace {

void printHex(const char* name, const uint8_t* p, size_t n) {
    if (name) printf("%s ", name);
    for (size_t i = 0; i < n; ++i) printf("%02x", p[i]);
    printf("\n");
}

std::vector<uint8_t> fromHex(const char* s) {
    std::vector<uint8_t> out;
    for (size_t i = 0; s[i] && s[i + 1]; i += 2) {
        unsigned v;
        if (sscanf(s + i, "%2x", &v) != 1) break;
        out.push_back(static_cast<uint8_t>(v));
    }
    return out;
}

struct Rig {
    tools::Identity id;
    fakes::Source src;
    fakes::Led led;
    mcp::Uplink up;
    mcp::MCPServer srv;
    tools::EdgeTools edge;
    Rig() {
        fakes::identity(id);
        up.begin(8, 2048, false);
        tools::EdgeToolsConfig c;
        c.identity = &id;
        c.source = &src;
        c.indicator = &led;
        c.uplink = &up;
        edge.begin(c, srv);
        mcp::ServerConfig sc;
        sc.workerCore = -1;
        srv.begin(sc, up);
    }
    ~Rig() { srv.shutdown(); }
};

class NullHw final : public tools::ActuatorHw {
public:
    bool configure(uint8_t, uint8_t, int, uint8_t) override { return true; }
    float write(uint8_t, uint8_t, float v) override { return v; }
};

/** The device composition of main.cpp, minus ESP-IDF: tools + outputs + NetObserve. */
struct NetRig {
    tools::Identity id;
    fakes::Source src;
    mcp::Uplink up;
    mcp::MCPServer srv;
    tools::EdgeTools edge;
    tools::ActuatorBank bank;
    NullHw hw;
    tools::SystemTools sys;
    nf::Platform plat;
    netobs::NetObserve nob;
    nf::Upstream u1;
    bool ok = true;

    NetRig() {
        fakes::identity(id);
        const uint16_t port = static_cast<uint16_t>(30000 + getpid() % 3000);
        ok = u1.start("127.0.0.1", port) && up.begin(8, 2048, false);
        bank.addChannel("pwm0", EWP_ACT_PWM, 5, 0.0f, false);
        bank.addChannel("relay0", EWP_ACT_RELAY, 7, 0.0f, true);
        bank.begin(hw, nullptr, nullptr);
        id.actuators = &bank;
        id.net = &nob;
        netobs::Config c;
        c.forward = true;
        c.port = 0;
        c.loopbackOnly = true;
        c.upstreams[0] = 0x7F000001;
        c.upstreamCount = 1;
        c.upstreamPort = port;
        c.batchPeriodMs = 100;
        c.mdnsPort = 0;
        c.dhcpPort = 0;
        c.preferPsram = false;
        ok = ok && nob.begin(c, plat, up);
        tools::EdgeToolsConfig ec;
        ec.identity = &id;
        ec.source = &src;
        ec.uplink = &up;
        ok = ok && edge.begin(ec, srv);
        tools::SystemToolsConfig sc;
        sc.actuators = &bank;
        sc.edge = &edge;
        sc.scheduleRestart = [](void*, uint32_t) {};
        ok = ok && sys.begin(sc, srv) && nob.registerTools(srv);
        mcp::ServerConfig cfg;
        cfg.workerCore = -1;
        ok = ok && srv.begin(cfg, up);
        plat.arp[0x7F000001] = {0x3c, 0x22, 0xfb, 0x12, 0x34, 0x56};
    }
    ~NetRig() {
        srv.shutdown();
        nob.stop();
    }
};

int netobsMode(int argc, char** argv) {
    NetRig rig;
    if (!rig.ok) return 1;
    nf::Client c;
    nf::Bytes resp;
    if (!nf::exchange(rig.nob, c, nf::query(0x1234, "www.youtube.com"), resp)) return 1;
    if (!nf::exchange(rig.nob, c, nf::query(0x1235, "example.org", 28), resp)) return 1;
    tf::Out o;
    if (!tf::pop(rig.up, o, 500)) return 1;
    printHex("flag", o.bytes.data(), o.bytes.size());
    nf::pump(rig.nob, 250);
    if (!tf::pop(rig.up, o, 500)) return 1;
    printHex("batch", o.bytes.data(), o.bytes.size());
    uint8_t buf[2048];
    size_t n = tools::buildDeviceFrame(buf, sizeof buf, EWP_MSG_HELLO, 1,
                                       [&](ewp::CborWriter& w) { rig.edge.encodeHello(w); });
    printHex("hello", buf, n);
    tools::TelemetrySnapshot s;
    rig.src.collect(s, tools::kSupportedSections);
    s.actuatorCount = rig.bank.values(s.actuators, tools::kMaxActuators);
    rig.nob.counters(s.dns);
    s.hasDns = true;
    n = tools::buildDeviceFrame(buf, sizeof buf, EWP_MSG_TELEMETRY, 2,
                                [&](ewp::CborWriter& w) { tools::encodeTelemetryBody(w, s, tools::kSupportedSections); });
    printHex("telemetry", buf, n);
    for (int i = 2; i < argc; ++i) {
        const std::vector<uint8_t> f = fromHex(argv[i]);
        rig.srv.onDownlink(f.data(), f.size(), ewp::Topic::Unicast);
        if (tf::pop(rig.up, o, 2000)) printHex("reply", o.bytes.data(), o.bytes.size());
    }
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: ewp_dump frames | req <hex>...\n");
        return 2;
    }
    if (strcmp(argv[1], "netobs") == 0) return netobsMode(argc, argv);
    Rig rig;
    if (strcmp(argv[1], "frames") == 0) {
        uint8_t buf[2048];
        size_t n = tools::buildDeviceFrame(buf, sizeof buf, EWP_MSG_HELLO, 1,
                                           [&](ewp::CborWriter& w) { rig.edge.encodeHello(w); });
        printHex("hello", buf, n);
        const uint8_t ip[4] = {192, 168, 0, 42};
        n = tools::buildDeviceFrame(buf, sizeof buf, EWP_MSG_STATUS, 2, [&](ewp::CborWriter& w) {
            tools::encodeStatusBody(w, EWP_STATE_ONLINE, EWP_MODE_ACTIVE, ip, 4, 0);
        });
        printHex("status", buf, n);
        n = tools::buildDeviceFrame(buf, sizeof buf, EWP_MSG_STATUS, 0, [&](ewp::CborWriter& w) {
            tools::encodeStatusBody(w, EWP_STATE_OFFLINE, -1, nullptr, 0, 0);
        });
        printHex("will", buf, n);
        rig.srv.postTick();  // periodic TELEMETRY through the real tick path
        tf::Out o;
        if (tf::pop(rig.up, o, 2000)) printHex("telemetry", o.bytes.data(), o.bytes.size());
        return 0;
    }
    if (strcmp(argv[1], "req") == 0) {
        for (int i = 2; i < argc; ++i) {
            const std::vector<uint8_t> f = fromHex(argv[i]);
            rig.srv.onDownlink(f.data(), f.size(), ewp::Topic::Unicast);
        }
        for (const tf::Out& o : tf::drain(rig.up, 300)) printHex(nullptr, o.bytes.data(), o.bytes.size());
        return 0;
    }
    return 2;
}
