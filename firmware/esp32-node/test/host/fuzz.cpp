/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/fuzz.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Randomised robustness runs for every parser that sees network or serial
 * input, built with ASan + UBSan (any out-of-bounds access or UB aborts):
 *
 *   cbor     random and mutated CBOR into CborReader (typed reads + skip)
 *   frames   random and mutated EWP frames through MCPServer + the real tools
 *   prov     random and mutated prov/2 lines through ProvHandler (+ CRC suffix)
 *   url      random strings into parseHostUrl / validators
 *
 *   ./fw_fuzz [iterations] [seed]
 * Deterministic for a given seed (xorshift64*), so a failure is reproducible.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>
#include <vector>

#include "ewp/cbor.h"
#include "ewp/crc32.h"
#include "ewp/frame.h"
#include "fakes.h"
#include "frames.h"
#include "mcp/MCPServer.h"
#include "prov/Json.h"
#include "prov/LineAssembler.h"
#include "prov/ProvHandler.h"
#include "prov/Validate.h"
#include "prov_fakes.h"
#include "tools/EdgeTools.h"

namespace {

uint64_t g_state = 0x9E3779B97F4A7C15ull;
uint64_t rnd() {
    g_state ^= g_state >> 12;
    g_state ^= g_state << 25;
    g_state ^= g_state >> 27;
    return g_state * 0x2545F4914F6CDD1Dull;
}
size_t below(size_t n) { return n == 0 ? 0 : static_cast<size_t>(rnd() % n); }

/** Byte-level mutations: flip, insert, delete, overwrite with "interesting" values, truncate. */
void mutate(std::vector<uint8_t>& v) {
    const int rounds = 1 + static_cast<int>(below(4));
    static const uint8_t interesting[] = {0x00, 0x01, 0x17, 0x18, 0x19, 0x1a, 0x1b, 0x1f, 0x7f, 0x80,
                                          0x9f, 0xa0, 0xbf, 0xf4, 0xf9, 0xfa, 0xfb, 0xff, 0xe5};
    for (int r = 0; r < rounds; ++r) {
        switch (below(6)) {
            case 0:
                if (!v.empty()) v[below(v.size())] ^= static_cast<uint8_t>(1u << below(8));
                break;
            case 1: v.insert(v.begin() + static_cast<long>(below(v.size() + 1)), static_cast<uint8_t>(rnd())); break;
            case 2:
                if (!v.empty()) v.erase(v.begin() + static_cast<long>(below(v.size())));
                break;
            case 3:
                if (!v.empty()) v[below(v.size())] = interesting[below(sizeof interesting)];
                break;
            case 4:
                if (!v.empty()) v.resize(below(v.size()));
                break;
            default: {
                const size_t n = below(8);
                for (size_t k = 0; k < n; ++k) v.push_back(static_cast<uint8_t>(rnd()));
            }
        }
    }
}

std::vector<uint8_t> randomBytes(size_t max) {
    std::vector<uint8_t> v(below(max + 1));
    for (auto& b : v) b = static_cast<uint8_t>(rnd());
    return v;
}

/* ---- cbor ------------------------------------------------------------------ */

void fuzzCborOnce(const std::vector<uint8_t>& in) {
    {
        ewp::CborReader r(in.data(), in.size());
        int guard = 0;
        while (!r.atEnd() && r.ok() && guard++ < 512) {
            if (!r.skip()) break;
        }
    }
    {
        ewp::CborReader r(in.data(), in.size());
        for (int i = 0; i < 64 && !r.atEnd() && r.ok(); ++i) {
            uint64_t u;
            int64_t s;
            double d;
            bool b;
            const char* t;
            const uint8_t* p;
            size_t n;
            uint32_t c;
            switch (r.peek()) {
                case ewp::CborType::UInt: r.readUint(u); break;
                case ewp::CborType::NInt: r.readInt(s); break;
                case ewp::CborType::Float: r.readNumber(d); break;
                case ewp::CborType::Bool: r.readBool(b); break;
                case ewp::CborType::Text: r.readText(t, n); break;
                case ewp::CborType::Bytes: r.readBytes(p, n); break;
                case ewp::CborType::Array: r.readArray(c); break;
                case ewp::CborType::Map: r.readMap(c); break;
                case ewp::CborType::Null: r.readNull(); break;
                default: r.skip(); break;
            }
        }
    }
}

void fuzzCbor(long iterations) {
    std::vector<std::vector<uint8_t>> seeds = {
        {0xa3, 0x01, 0x03, 0x02, 0xa2, 0x01, 0x00, 0x02, 0x19, 0x03, 0xe8, 0x03, 0x03},
        {0xa1, 0x01, 0x18, 0x3f},
        {0x82, 0x83, 0x02, 0x01, 0x09, 0x83, 0x01, 0x01, 0x09},
        {0xfa, 0x3f, 0xc0, 0x00, 0x00},
        {0x62, 0xc3, 0xa9},
    };
    for (long i = 0; i < iterations; ++i) {
        std::vector<uint8_t> v = (i % 3 == 0) ? randomBytes(64) : seeds[below(seeds.size())];
        if (i % 3 != 0) mutate(v);
        fuzzCborOnce(v);
    }
}

/* ---- frames through the server -------------------------------------------- */

void fuzzFrames(long iterations) {
    tools::Identity id;
    fakes::identity(id);
    fakes::Source src;
    fakes::Led led;
    mcp::Uplink up;
    up.begin(8, 2048, false);
    mcp::MCPServer srv;
    tools::EdgeTools edge;
    tools::EdgeToolsConfig c;
    c.identity = &id;
    c.source = &src;
    c.indicator = &led;
    c.uplink = &up;
    edge.begin(c, srv);
    mcp::ServerConfig sc;
    sc.workerCore = -1;
    sc.broadcastJitterMs = 0;
    srv.begin(sc, up);

    const tf::Bytes seeds[] = {
        tf::req(1, EWP_TOOL_READ_TELEMETRY, EWP_FLAG_ACK_REQUESTED, 1000, {0xa1, 0x01, 0x18, 0x3f}),
        tf::req(2, EWP_TOOL_GET_DEVICE_INFO),
        tf::req(3, EWP_TOOL_IDENTIFY, 0, 0, {0xa1, 0x01, 0x19, 0x04, 0xb0}),
        tf::cancel(1),
    };
    for (long i = 0; i < iterations; ++i) {
        tf::Bytes f = seeds[below(4)];
        if (i % 4 != 0) {
            // keep the header mostly sane so the CBOR body is what gets exercised
            tf::Bytes body(f.begin() + EWP_HEADER_LEN, f.end());
            mutate(body);
            if (body.size() > 200) body.resize(200);
            ewp::RawHeader raw;
            ewp::decodeHeader(f.data(), f.size(), raw);
            raw.h.bodyLen = static_cast<uint16_t>(body.size());
            raw.h.rid = static_cast<uint32_t>(rnd());
            f.assign(EWP_HEADER_LEN, 0);
            ewp::encodeHeader(raw.h, f.data());
            f.insert(f.end(), body.begin(), body.end());
        } else {
            mutate(f);  // header damage too
        }
        srv.onDownlink(f.data(), f.size(), (i & 8) ? ewp::Topic::Broadcast : ewp::Topic::Unicast);
        tf::Out o;
        while (tf::pop(up, o, 1)) {
            if (!tf::wellFormed(o.body(), o.bodyLen())) {
                fprintf(stderr, "malformed uplink frame at iteration %ld\n", i);
                abort();
            }
        }
    }
    for (int k = 0; k < 50 && srv.inFlight() > 0; ++k) {
        tf::Out o;
        tf::pop(up, o, 20);
    }
    tf::drain(up, 100);
    srv.shutdown();
    if (up.freeSlots() != up.slots()) {
        fprintf(stderr, "uplink slots leaked: %zu of %zu free\n", up.freeSlots(), up.slots());
        abort();
    }
}

/* ---- provisioning lines ------------------------------------------------------ */

void fuzzProv(long iterations) {
    pf::MemKv kv;
    prov::ConfigStore cfg;
    cfg.begin(kv);
    prov::LoadReport rep;
    cfg.load(rep);
    pf::FakeBackend be;
    prov::ProvHandler h;
    be.handler = &h;
    be.cfg = &cfg;
    size_t bytesOut = 0;
    h.begin(cfg, be, pf::board(), [](void* u, const char*, size_t n) { *static_cast<size_t*>(u) += n; }, &bytesOut);
    const char* seeds[] = {
        "{\"ionity\":\"prov\",\"op\":\"hello\"}",
        "{\"ionity\":\"prov\",\"op\":\"set\",\"v\":2,\"id\":7,\"ssid\":\"IONITY-LAB-IOT\",\"pass\":\"lab-pass-123\","
        "\"host_url\":\"mqtts://fleet.ionity.lan\",\"site\":\"lab\",\"pins\":{\"pwm0\":5}}",
        "{\"ionity\":\"prov\",\"op\":\"test\",\"v\":2,\"id\":8,\"timeout_ms\":20000}",
        "{\"ionity\":\"prov\",\"op\":\"scan\",\"v\":2,\"id\":9}",
        "{\"ionity\":\"prov\",\"op\":\"set\",\"server\":\"10.0.0.1\",\"mqtt_port\":1884}",
    };
    static prov::LineAssembler<prov::ProvHandler::kLineMax> la;
    for (long i = 0; i < iterations; ++i) {
        const char* s = seeds[below(5)];
        std::vector<uint8_t> v(s, s + strlen(s));
        mutate(v);
        std::string line(v.begin(), v.end());
        if (rnd() & 1) {  // a valid CRC over the mutated text exercises the JSON paths
            char hex[9];
            ewp::crc32_hex(ewp::crc32(line.data(), line.size()), hex);
            line += " *";
            line += hex;
        }
        line += (rnd() & 1) ? "\r\n" : "\n";
        la.feed(reinterpret_cast<const uint8_t*>(line.data()), line.size(),
                [&](const char* l, size_t n) { h.onLine(l, n); }, [&] { h.onOverlong(); });
        if (be.restarts > 0) be.restarts = 0;
        if (i % 1000 == 0) {  // keep NVS from growing without bound across iterations
            cfg.eraseAll();
        }
    }
    if (bytesOut == 0) abort();
}

/* ---- validators -------------------------------------------------------------- */

void fuzzUrl(long iterations) {
    const char* seeds[] = {"mqtts://Fleet.Ionity.LAN:8883", "wss://gw/ewp", "[fd00::1]:1884", "192.168.0.3",
                           "mqtt://a.b-c.d"};
    for (long i = 0; i < iterations; ++i) {
        std::vector<uint8_t> v;
        if (i % 2) {
            const char* s = seeds[below(5)];
            v.assign(s, s + strlen(s));
            mutate(v);
        } else {
            v = randomBytes(150);
        }
        const char* p = reinterpret_cast<const char*>(v.data());
        prov::HostUrl u;
        const char* err = nullptr;
        if (prov::parseHostUrl(p, v.size(), u, &err)) {
            if (strlen(u.href) >= sizeof u.href) abort();
        }
        char uuid[37];
        prov::normalizeUuid(p, v.size(), uuid);
        prov::validPemCert(p, v.size(), &err);
        prov::validWifiPass(p, v.size(), &err);
        prov::validSsid(p, v.size(), &err);
        prov::jsonShapeOk(p, v.size(), 64, 4);
        char out[64];
        prov::JsonWriter w(out, sizeof out);
        w.str(p, v.size());  // invalid UTF-8 -> U+FFFD, overflow -> error flag
    }
}

}  // namespace

int main(int argc, char** argv) {
    const long n = argc > 1 ? atol(argv[1]) : 20000;
    if (argc > 2) g_state = strtoull(argv[2], nullptr, 0) | 1;
    printf("fuzz: %ld iterations per target, seed 0x%llx\n", n, static_cast<unsigned long long>(g_state));
    fuzzCbor(n);
    printf("  cbor   ok\n");
    fuzzFrames(n / 4);
    printf("  frames ok\n");
    fuzzProv(n / 4);
    printf("  prov   ok\n");
    fuzzUrl(n);
    printf("  url    ok\n");
    return 0;
}
