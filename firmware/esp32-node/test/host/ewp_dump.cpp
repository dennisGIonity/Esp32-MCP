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
 */
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

#include "fakes.h"
#include "frames.h"
#include "mcp/MCPServer.h"
#include "tools/EdgeTools.h"
#include "tools/Identity.h"

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

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: ewp_dump frames | req <hex>...\n");
        return 2;
    }
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
