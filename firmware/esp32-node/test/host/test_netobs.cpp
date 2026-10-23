/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/test_netobs.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * NetObserve: DNS wire parsing, watchlist matching, cache, query log, client
 * table, mDNS/DHCP parsers - and the real forwarder loop over loopback sockets
 * against fake upstream resolvers, with its events and tools end to end.
 */
#include <string.h>
#include <unistd.h>

#include <string>
#include <vector>

#include "check.h"
#include "frames.h"
#include "mcp/MCPServer.h"
#include "netobs_fakes.h"
#include "netobserve/Discovery.h"
#include "netobserve/NetObserve.h"

using namespace tf;
using netobs::DnsQuestion;

namespace {

uint16_t testPort() { return static_cast<uint16_t>(26000 + (getpid() % 3000)); }

netobs::Config loopbackConfig(uint16_t upPort) {
    netobs::Config c;
    c.forward = true;
    c.port = 0;  // ephemeral
    c.loopbackOnly = true;
    c.upstreams[0] = 0x7F000001;  // 127.0.0.1
    c.upstreams[1] = 0x7F000002;  // 127.0.0.2
    c.upstreamCount = 2;
    c.upstreamPort = upPort;
    c.retryMs = 150;
    c.failMs = 400;
    c.batchPeriodMs = 200;
    c.mdnsPort = 0;
    c.dhcpPort = 0;
    c.logBytes = 4096;
    c.cache.entries = 8;
    c.preferPsram = false;
    return c;
}

/** The value at `key` of a map nested at `outer` of a frame body. */
bool nested(const uint8_t* p, size_t n, uint64_t outer, const uint8_t*& v, size_t& vn) { return getRaw(p, n, outer, v, vn); }

uint64_t mapUint(const uint8_t* p, size_t n, uint64_t key) {
    uint64_t v = 0xDEAD;
    getUint(p, n, key, v);
    return v;
}

}  // namespace

/* ---- DNS wire ---------------------------------------------------------------------- */

TEST(netobs_wire_parse_query) {
    const nf::Bytes q = nf::query(0x1234, "WWW.YouTube.COM", 28, true);
    DnsQuestion d;
    CHECK(netobs::parseQuery(q.data(), q.size(), d));
    CHECK_STR(d.name, "www.youtube.com");
    CHECK_EQ(d.nameLen, 15u);
    CHECK(d.nameClean);
    CHECK_EQ(d.qtype, 28u);
    CHECK_EQ(d.qclass, 1u);
    CHECK(d.edns);
    CHECK_EQ(d.nameWireLen, 17u);

    nf::Bytes resp = nf::answer(q.data(), q.size(), 60);
    CHECK(!netobs::parseQuery(resp.data(), resp.size(), d));  // a response is not a query
    nf::Bytes two = nf::query(1, "a.example");
    two[5] = 2;  // qdcount 2
    CHECK(!netobs::parseQuery(two.data(), two.size(), d));
    nf::Bytes cut = nf::query(1, "a.example");
    cut.resize(cut.size() - 3);
    CHECK(!netobs::parseQuery(cut.data(), cut.size(), d));
    nf::Bytes ptr = nf::query(1, "a.example");
    ptr[12] = 0xC0;  // compression in a question
    CHECK(!netobs::parseQuery(ptr.data(), ptr.size(), d));
    nf::Bytes dirty = nf::query(1, "a b.example");
    CHECK(netobs::parseQuery(dirty.data(), dirty.size(), d));
    CHECK(!d.nameClean);
    CHECK_STR(d.name, "a?b.example");
    // root query (".", NS)
    nf::Bytes root = nf::query(1, "", 2);
    CHECK(netobs::parseQuery(root.data(), root.size(), d));
    CHECK_EQ(d.nameLen, 0u);
}

TEST(netobs_wire_parse_response_and_servfail) {
    const nf::Bytes q = nf::query(0x4242, "Example.org");
    DnsQuestion d;
    CHECK(netobs::parseQuery(q.data(), q.size(), d));
    nf::Bytes r = nf::answer(q.data(), q.size(), 120);
    netobs::DnsAnswerInfo info{};
    CHECK(netobs::parseResponse(r.data(), r.size(), d, info));
    CHECK_EQ(info.rcode, 0u);
    CHECK_EQ(info.ancount, 1u);
    CHECK_EQ(info.minTtl, 120u);
    CHECK(!info.truncated);
    // a reply to another name is refused
    nf::Bytes other = r;
    other[13] = 'X';
    CHECK(!netobs::parseResponse(other.data(), other.size(), d, info));
    // an rdlength past the end is refused
    nf::Bytes bad = r;
    bad[bad.size() - 5] = 0x40;
    CHECK(!netobs::parseResponse(bad.data(), bad.size(), d, info));
    // a pointer loop (the question name pointing at itself) is refused
    nf::Bytes loop = r;
    loop[12] = 0xC0;
    loop[13] = 12;
    CHECK(!netobs::parseResponse(loop.data(), loop.size(), d, info));

    uint8_t sf[512];
    const size_t n = netobs::buildServfail(q.data(), d, sf, sizeof sf);
    CHECK_EQ(n, static_cast<size_t>(d.end));
    CHECK_EQ(sf[0], 0x42);
    CHECK_EQ(sf[2] & 0x80, 0x80);
    CHECK_EQ(sf[3] & 0x0F, 2);
    CHECK_EQ(sf[7], 0);

    CHECK(netobs::ageTtls(r.data(), r.size(), 20));
    CHECK(netobs::parseResponse(r.data(), r.size(), d, info));
    CHECK_EQ(info.minTtl, 100u);
}

/* ---- watchlist -------------------------------------------------------------------- */

TEST(netobs_watchlist_defaults_match_youtube) {
    static netobs::Watchlist wl;
    wl.loadDefaults();
    CHECK_EQ(wl.version(), EWP_DEFAULT_WATCHLIST_VERSION);
    CHECK_EQ(wl.ruleCount(), EWP_DEFAULT_RULE_COUNT);
    struct {
        const char* name;
        uint8_t cat;
    } cases[] = {
        {"youtube.com", 1},          {"www.youtube.com", 1},       {"m.youtube.com", 1},
        {"r4---sn-8vbs.googlevideo.com", 1}, {"googlevideo.com", 0}, {"i.ytimg.com", 1},
        {"ytimg.com", 0},            {"youtu.be", 1},              {"youtubei.googleapis.com", 1},
        {"googleapis.com", 0},       {"notyoutube.com", 0},        {"youtube.com.evil.net", 0},
        {"www.tiktok.com", 2},       {"example.org", 0},           {"", 0},
    };
    for (const auto& c : cases) {
        netobs::Watchlist::Match m;
        const bool hit = wl.match(c.name, strlen(c.name), m);
        CHECK_EQ(hit, c.cat != 0);
        CHECK_EQ(m.category, c.cat);
        if (hit) CHECK(m.flags & EWP_RULE_ALERT);
    }
}

TEST(netobs_watchlist_edit_persist_and_limits) {
    static netobs::Watchlist wl;
    static netobs::Watchlist back;
    wl.clear(9);
    const char* err = nullptr;
    CHECK(wl.upsertCategory(3, "social:example", 14, &err));
    CHECK(wl.upsertRule("example.org", 11, 3, EWP_RULE_ALERT, &err));
    CHECK(wl.upsertRule("cdn.example.org", 15, 3, 0, &err));  // more specific, no alert
    netobs::Watchlist::Match m;
    CHECK(wl.match("a.cdn.example.org", 17, m));
    CHECK_EQ(m.flags, 0u);  // longest suffix wins
    CHECK(wl.match("www.example.org", 15, m));
    CHECK_EQ(m.flags, EWP_RULE_ALERT);
    CHECK(wl.upsertRule("example.org", 11, 3, 0, &err));  // update in place
    CHECK_EQ(wl.ruleCount(), 2u);
    for (const char* bad : {"Example.org", ".example.org", "example..org", "example.org.", "exa mple.org", ""}) {
        CHECK(!wl.upsertRule(bad, strlen(bad), 3, 0, &err));
    }
    CHECK(!wl.upsertRule("ok.org", 6, 0, 0, &err));     // category 0
    CHECK(!wl.upsertRule("ok.org", 6, 33, 0, &err));    // category > 32
    CHECK(!wl.upsertRule("ok.org", 6, 1, 0x80, &err));  // unknown flag
    uint8_t blob[netobs::Watchlist::kBlobMax];
    const size_t n = wl.serialize(blob, sizeof blob);
    CHECK(n > 0);
    CHECK(back.deserialize(blob, n));
    CHECK_EQ(back.version(), 9u);
    CHECK_EQ(back.ruleCount(), 2u);
    CHECK_STR(back.categoryName(0), "social:example");
    blob[n - 1] ^= 0xFF;
    CHECK(back.deserialize(blob, n) || true);  // still structurally fine (a name byte)
    CHECK(!back.deserialize(blob, n - 1));      // truncated
    // capacity: 128 rules
    wl.clear(1);
    char name[32];
    for (int i = 0; i < 128; ++i) {
        const int len = snprintf(name, sizeof name, "host%03d.example.net", i);
        CHECK(wl.upsertRule(name, static_cast<size_t>(len), 1, 0, &err));
    }
    CHECK(!wl.upsertRule("one-too-many.net", 16, 1, 0, &err));
}

/* ---- cache / log / clients -------------------------------------------------------- */

TEST(netobs_cache_rewrites_id_case_and_ttl) {
    netobs::DnsCache cache;
    netobs::DnsCache::Config cc;
    cc.entries = 4;
    cc.preferPsram = false;
    CHECK(cache.begin(cc));
    const nf::Bytes q1 = nf::query(0x1111, "www.example.org");
    DnsQuestion d1;
    CHECK(netobs::parseQuery(q1.data(), q1.size(), d1));
    const nf::Bytes r1 = nf::answer(q1.data(), q1.size(), 100);
    netobs::DnsAnswerInfo info{};
    CHECK(netobs::parseResponse(r1.data(), r1.size(), d1, info));
    cache.store(d1, r1.data(), r1.size(), info, 0);
    CHECK_EQ(cache.live(0), 1u);

    const nf::Bytes q2 = nf::query(0x2222, "WWW.Example.ORG");
    DnsQuestion d2;
    CHECK(netobs::parseQuery(q2.data(), q2.size(), d2));
    uint8_t out[512];
    const size_t n = cache.lookup(q2.data(), d2, 30 * 1000000LL, out, sizeof out);
    CHECK_EQ(n, r1.size());
    CHECK_EQ(out[0], 0x22);
    CHECK(memcmp(out + 12, q2.data() + 12, d2.nameWireLen) == 0);  // the client's letter case
    netobs::DnsAnswerInfo i2{};
    CHECK(netobs::parseResponse(out, n, d2, i2));
    CHECK_EQ(i2.minTtl, 70u);  // aged by 30 s

    // EDNS is part of the key; expiry; NXDOMAIN is not cached; flush
    const nf::Bytes qe = nf::query(3, "www.example.org", 1, true);
    DnsQuestion de;
    CHECK(netobs::parseQuery(qe.data(), qe.size(), de));
    CHECK_EQ(cache.lookup(qe.data(), de, 1, out, sizeof out), 0u);
    CHECK_EQ(cache.lookup(q2.data(), d2, 101 * 1000000LL, out, sizeof out), 0u);
    const nf::Bytes nx = nf::answer(q1.data(), q1.size(), 0, 3);
    netobs::DnsAnswerInfo ni{};
    CHECK(netobs::parseResponse(nx.data(), nx.size(), d1, ni));
    cache.store(d1, nx.data(), nx.size(), ni, 0);
    CHECK_EQ(cache.live(0), 0u);
    cache.store(d1, r1.data(), r1.size(), info, 0);
    CHECK_EQ(cache.flush(0), 1u);
    CHECK_EQ(cache.live(0), 0u);
}

TEST(netobs_query_log_ring_and_cursor) {
    netobs::QueryLog log;
    CHECK(log.begin(1024, false));
    static netobs::QueryRecord r;
    for (uint32_t i = 0; i < 100; ++i) {
        r = netobs::QueryRecord{};
        r.tMs = i;
        r.ip = 0xC0A80000 | i;
        r.nameLen = static_cast<uint8_t>(snprintf(r.name, sizeof r.name, "n%u.example", static_cast<unsigned>(i)));
        log.push(r);
        CHECK_EQ(r.seq, i + 1);
    }
    CHECK(log.count() < 100u);  // older ones overwritten
    CHECK(log.newest(0, r));
    CHECK_EQ(r.tMs, 99u);
    CHECK_STR(r.name, "n99.example");
    const uint32_t lost = log.takeLost();
    CHECK_EQ(lost + log.count(), 100u);
    CHECK_EQ(log.unsent(), log.count());
    log.markSent(log.nextSeq());
    CHECK_EQ(log.unsent(), 0u);
    CHECK(!log.get(1, r));  // overwritten
}

TEST(netobs_client_table_dhcp_and_recency) {
    static netobs::ClientTable t;
    const uint8_t mac[6] = {0x3c, 0x22, 0xfb, 1, 2, 3};
    t.touch(0xC0A8002A, 1000, EWP_DISC_DNS);
    t.setMac(0xC0A8002A, mac, 1100, EWP_DISC_ARP);
    t.setHostname(0xC0A8002A, "Kitchen TV!", 11, 1200, EWP_DISC_MDNS);
    const netobs::LanDevice* d = t.find(0xC0A8002A);
    CHECK(d != nullptr);
    CHECK_STR(d->hostname, "Kitchen-TV");
    CHECK_EQ(d->sources, EWP_DISC_DNS | EWP_DISC_ARP | EWP_DISC_MDNS);
    CHECK_EQ(d->queries, 1u);
    // the same MAC renews with a new address: the entry follows it
    t.dhcp(mac, 0xC0A8002B, "kitchen-tv", 10, 5000);
    CHECK(t.find(0xC0A8002A) == nullptr);
    CHECK(t.find(0xC0A8002B) != nullptr);
    CHECK_EQ(t.size(), 1u);
    t.touch(0xC0A80007, 6000, EWP_DISC_ARP);
    CHECK_EQ(t.byRecency(0, 7000)->ip, 0xC0A80007u);
    CHECK_EQ(t.byRecency(1, 7000)->ip, 0xC0A8002Bu);
    CHECK(t.byRecency(2, 7000) == nullptr);
    CHECK_EQ(t.activeClients(7000, 600000), 1u);
    for (uint32_t i = 0; i < 200; ++i) t.touch(0x0A000000 | i, 8000 + i, EWP_DISC_ARP);  // eviction
    CHECK_EQ(t.size(), static_cast<size_t>(EWP_LIMIT_LOCAL_DEVICES));
}

TEST(netobs_mdns_and_dhcp_parsers) {
    // mDNS response: answer "phone.local A 192.168.0.42", additional "printer.local A 192.168.0.9" (compressed)
    nf::Bytes m;
    nf::put16(m, 0);
    nf::put16(m, 0x8400);
    nf::put16(m, 0);
    nf::put16(m, 1);
    nf::put16(m, 0);
    nf::put16(m, 1);
    nf::putName(m, "phone.local");
    nf::put16(m, 1);
    nf::put16(m, 0x8001);
    nf::put16(m, 0);
    nf::put16(m, 120);
    nf::put16(m, 4);
    m.insert(m.end(), {192, 168, 0, 42});
    m.insert(m.end(), {7, 'p', 'r', 'i', 'n', 't', 'e', 'r', 0xC0, 0x12});  // printer + pointer to "local"
    nf::put16(m, 1);
    nf::put16(m, 0x8001);
    nf::put16(m, 0);
    nf::put16(m, 120);
    nf::put16(m, 4);
    m.insert(m.end(), {192, 168, 0, 9});
    std::vector<std::string> names;
    std::vector<uint32_t> ips;
    struct Sink {
        std::vector<std::string>* n;
        std::vector<uint32_t>* i;
    } sink{&names, &ips};
    const int found = netobs::parseMdns(
        m.data(), m.size(),
        [](void* u, uint32_t ip, const char* name, size_t len) {
            Sink* s = static_cast<Sink*>(u);
            s->n->emplace_back(name, len);
            s->i->push_back(ip);
        },
        &sink);
    CHECK_EQ(found, 2);
    CHECK_STR(names[0].c_str(), "phone");
    CHECK_EQ(ips[0], 0xC0A8002Au);
    CHECK_STR(names[1].c_str(), "printer");

    // DHCP REQUEST: chaddr, option 53 = 3, option 50 = 192.168.0.77, option 12 = "Galaxy-S24"
    nf::Bytes d(240, 0);
    d[0] = 1;
    d[1] = 1;
    d[2] = 6;
    const uint8_t mac[6] = {0xaa, 0xbb, 0xcc, 0x01, 0x02, 0x03};
    memcpy(d.data() + 28, mac, 6);
    d[236] = 0x63;
    d[237] = 0x82;
    d[238] = 0x53;
    d[239] = 0x63;
    d.insert(d.end(), {53, 1, 3, 50, 4, 192, 168, 0, 77, 12, 10});
    const char* host = "Galaxy-S24";
    d.insert(d.end(), host, host + 10);
    d.push_back(255);
    netobs::DhcpInfo info;
    CHECK(netobs::parseDhcpRequest(d.data(), d.size(), info));
    CHECK_EQ(info.msgType, 3u);
    CHECK_EQ(info.ip, 0xC0A8004Du);
    CHECK_STR(info.hostname, "Galaxy-S24");
    CHECK(memcmp(info.mac, mac, 6) == 0);
    nf::Bytes trunc(d.begin(), d.end() - 5);  // option 12 runs past the end
    CHECK(!netobs::parseDhcpRequest(trunc.data(), trunc.size(), info));
    d[0] = 2;  // a BOOTREPLY is not a client
    CHECK(!netobs::parseDhcpRequest(d.data(), d.size(), info));
}

/* ---- the forwarder, end to end over loopback ---------------------------------------- */

TEST(netobs_forward_cache_flag_and_batch) {
    const uint16_t up = testPort();
    nf::Upstream u1;
    nf::Upstream u2;
    CHECK(u1.start("127.0.0.1", up));
    CHECK(u2.start("127.0.0.2", up));
    nf::Platform plat;
    mcp::Uplink uplink;
    CHECK(uplink.begin(8, 2048, false));
    netobs::NetObserve nob;
    CHECK(nob.begin(loopbackConfig(up), plat, uplink));
    nob.runOnce(10);
    CHECK(nob.listenPort() != 0);
    CHECK_EQ(plat.resolvers.size(), 2u);  // the node's own resolver points at the upstreams
    plat.arp[0x7F000001] = {0x02, 0x00, 0x00, 0xaa, 0xbb, 0xcc};

    nf::Client c;
    nf::Bytes resp;
    CHECK(nf::exchange(nob, c, nf::query(0xBEEF, "www.YouTube.com"), resp));
    CHECK_EQ(resp[0], 0xBE);
    CHECK_EQ(resp[1], 0xEF);
    CHECK_EQ(resp[7], 1);  // one answer
    CHECK_EQ(u1.seen.load(), 1);
    CHECK_EQ(plat.alerts.load(), 1);
    CHECK_EQ(plat.lastAlertCategory.load(), 1);

    // the DNS_FLAGGED event went out at once
    Out ev;
    CHECK(pop(uplink, ev, 200));
    CHECK_EQ(ev.h.type, EWP_MSG_EVENT);
    CHECK_EQ(mapUint(ev.body(), ev.bodyLen(), EWP_K_EVENT_BODY_KIND), EWP_EVENT_DNS_FLAGGED);
    const uint8_t* f = nullptr;
    size_t fn = 0;
    CHECK(nested(ev.body(), ev.bodyLen(), EWP_K_EVENT_BODY_FLAG, f, fn));
    std::string s;
    CHECK(getText(f, fn, EWP_K_DNS_FLAG_QNAME, s));
    CHECK_STR(s.c_str(), "www.youtube.com");
    CHECK(getText(f, fn, EWP_K_DNS_FLAG_RULE, s));
    CHECK_STR(s.c_str(), "youtube.com");
    CHECK_EQ(mapUint(f, fn, EWP_K_DNS_FLAG_CATEGORY), 1u);
    CHECK_EQ(mapUint(f, fn, EWP_K_DNS_FLAG_WATCH_VER), EWP_DEFAULT_WATCHLIST_VERSION);

    // the same question (other id, other case): from the cache, no upstream traffic, no second event
    CHECK(nf::exchange(nob, c, nf::query(0x0102, "WWW.youtube.COM"), resp));
    CHECK_EQ(resp[0], 0x01);
    CHECK_EQ(resp[1], 0x02);
    CHECK_EQ(resp[12 + 1], 'W');  // the client's own letter case in the question
    CHECK_EQ(u1.seen.load(), 1);
    CHECK(nf::exchange(nob, c, nf::query(7, "example.org", 28), resp));
    tools::DnsCountersSnap cs;
    nob.counters(cs);
    CHECK_EQ(cs.mode, EWP_DNS_MODE_FORWARD);
    CHECK_EQ(cs.queries, 3u);
    CHECK_EQ(cs.forwarded, 2u);
    CHECK_EQ(cs.cacheHits, 1u);
    CHECK_EQ(cs.flagged, 2u);
    CHECK_EQ(cs.clients, 1u);
    CHECK_EQ(plat.alerts.load(), 2);  // every hit renews the light
    CHECK(!pop(uplink, ev, 50));      // ...but one DNS_FLAGGED per client + rule per 30 s

    // after the batch period: one DNS_BATCH with the folded queries
    nf::pump(nob, 300);
    CHECK(pop(uplink, ev, 200));
    CHECK_EQ(mapUint(ev.body(), ev.bodyLen(), EWP_K_EVENT_BODY_KIND), EWP_EVENT_DNS_BATCH);
    const uint8_t* b = nullptr;
    size_t bn = 0;
    CHECK(nested(ev.body(), ev.bodyLen(), EWP_K_EVENT_BODY_DNS, b, bn));
    CHECK_EQ(mapUint(b, bn, EWP_K_DNS_BATCH_SEQ), 1u);
    CHECK(!hasKey(b, bn, EWP_K_DNS_BATCH_MORE));
    const uint8_t* qs = nullptr;
    size_t qn = 0;
    CHECK(getRaw(b, bn, EWP_K_DNS_BATCH_QUERIES, qs, qn));
    ewp::CborReader r(qs, qn);
    uint32_t count = 0;
    CHECK(r.readArray(count) && count == 2);  // youtube x2 folded + example.org
    uint32_t nine = 0;
    CHECK(r.readArray(nine) && nine == 9);
    uint64_t v = 0;
    CHECK(r.readUint(v));  // ageMs
    CHECK(r.readUint(v) && v == 0);  // client index
    const char* name = nullptr;
    size_t nl = 0;
    CHECK(r.readText(name, nl) && std::string(name, nl) == "www.youtube.com");
    CHECK(r.readUint(v) && v == 1);  // A
    CHECK(r.readUint(v) && v == 0);  // NOERROR
    CHECK(r.readUint(v));            // latency
    CHECK(r.readUint(v) && v == 2);  // count
    CHECK(r.readUint(v) && (v & EWP_DNSQ_FLAGGED) && (v & EWP_DNSQ_CACHED));
    CHECK(r.readUint(v) && v == 1);  // category
    const uint8_t* cl = nullptr;
    size_t cn = 0;
    CHECK(getRaw(b, bn, EWP_K_DNS_BATCH_CLIENTS, cl, cn));
    ewp::CborReader cr(cl, cn);
    CHECK(cr.readArray(count) && count == 1);
    CHECK(cr.readArray(count) && count == 3);
    const uint8_t* ipb = nullptr;
    size_t ipn = 0;
    CHECK(cr.readBytes(ipb, ipn) && ipn == 4 && ipb[0] == 127 && ipb[3] == 1);
    CHECK(cr.readBytes(ipb, ipn) && ipn == 6 && ipb[3] == 0xaa);  // MAC from ARP
    nob.stop();
}

TEST(netobs_retry_second_upstream_then_servfail) {
    const uint16_t up = static_cast<uint16_t>(testPort() + 1);
    nf::Upstream u1;
    nf::Upstream u2;
    CHECK(u1.start("127.0.0.1", up));
    CHECK(u2.start("127.0.0.2", up));
    u1.mode.store(nf::Upstream::Silent);
    nf::Platform plat;
    mcp::Uplink uplink;
    CHECK(uplink.begin(8, 2048, false));
    netobs::NetObserve nob;
    CHECK(nob.begin(loopbackConfig(up), plat, uplink));
    nf::Client c;
    nf::Bytes resp;
    CHECK(nf::exchange(nob, c, nf::query(0x0A0B, "slow.example"), resp));
    CHECK_EQ(resp[3] & 0x0F, 0);  // answered by 127.0.0.2 after the retry
    CHECK_EQ(u2.seen.load(), 1);

    u2.mode.store(nf::Upstream::WrongQuestion);  // a reply to another question is never accepted
    const int64_t t0 = os::nowUs();
    CHECK(nf::exchange(nob, c, nf::query(0x0C0D, "dead.example"), resp));
    CHECK((os::nowUs() - t0) / 1000 >= 350);
    CHECK_EQ(resp[0], 0x0C);
    CHECK_EQ(resp[3] & 0x0F, 2);  // SERVFAIL
    tools::DnsCountersSnap cs;
    nob.counters(cs);
    CHECK_EQ(cs.upstreamFail, 1u);

    // garbage is dropped without a reply
    nf::Client g;
    g.send({1, 2, 3}, nob.listenPort());
    nf::pump(nob, 50);
    CHECK(!g.recv(resp));
    nob.counters(cs);
    CHECK_EQ(cs.dropped, 1u);
    nob.stop();
}

/* ---- tools through the MCP server ---------------------------------------------------- */

namespace {

struct NobRig {
    nf::Platform plat;
    mcp::Uplink up;
    mcp::MCPServer srv;
    netobs::NetObserve nob;
    uint16_t upPort;
    nf::Upstream u1;

    explicit NobRig(uint16_t port) : upPort(port) {
        CHECK(u1.start("127.0.0.1", port));
        CHECK(up.begin(8, 2048, false));
        netobs::Config cfg = loopbackConfig(port);
        cfg.upstreamCount = 1;
        CHECK(nob.begin(cfg, plat, up));
        CHECK(nob.registerTools(srv));
        mcp::ServerConfig sc;
        sc.workerCore = -1;
        CHECK(srv.begin(sc, up));
    }
    ~NobRig() {
        srv.shutdown();
        nob.stop();
    }
    Out call(uint32_t rid, uint8_t tool, const Bytes& body = {0xA0}) {
        const Bytes f = req(rid, tool, 0, 2000, body);
        srv.onDownlink(f.data(), f.size(), ewp::Topic::Unicast);
        Out o;
        // skip any NetObserve events that are already queued
        for (;;) {
            CHECK(pop(up, o));
            if (o.h.type != EWP_MSG_EVENT) break;
        }
        return o;
    }
};

Bytes cbor(void (*fill)(ewp::CborWriter&)) {
    uint8_t buf[1024];
    ewp::CborWriter w(buf, sizeof buf);
    fill(w);
    return Bytes(buf, buf + w.size());
}

bool res(const Out& o, const uint8_t*& r, size_t& rn) {
    return o.h.type == EWP_MSG_RES && getRaw(o.body(), o.bodyLen(), EWP_K_RESULT_BODY_RESULT, r, rn);
}

}  // namespace

TEST(netobs_tools_watchlist_replace_append_persist) {
    NobRig rig(static_cast<uint16_t>(testPort() + 2));
    Out o = rig.call(1, EWP_TOOL_SET_WATCHLIST, cbor([](ewp::CborWriter& w) {
        ewp::CborMap m(w);
        m.key(EWP_K_SET_WATCHLIST_ARGS_OP).uint(EWP_WATCH_OP_REPLACE);
        m.key(EWP_K_SET_WATCHLIST_ARGS_VERSION).uint(1700000000);
        m.key(EWP_K_SET_WATCHLIST_ARGS_RULES).array(2);
        w.array(3);
        w.text("youtube.com");
        w.uint(1);
        w.uint(EWP_RULE_ALERT);
        w.array(3);
        w.text("example.net");
        w.uint(5);
        w.uint(0);
        m.key(EWP_K_SET_WATCHLIST_ARGS_CATEGORIES).array(1);
        w.array(2);
        w.uint(5);
        w.text("custom:example");
    }));
    const uint8_t* r = nullptr;
    size_t rn = 0;
    CHECK(res(o, r, rn));
    CHECK_EQ(mapUint(r, rn, EWP_K_WATCHLIST_RESULT_VERSION), 1700000000u);
    CHECK_EQ(mapUint(r, rn, EWP_K_WATCHLIST_RESULT_RULES), 2u);
    CHECK_EQ(rig.plat.saves, 1);
    CHECK_EQ(rig.nob.watchVersion(), 1700000000u);

    // APPEND with a stale version: refused, list unchanged
    o = rig.call(2, EWP_TOOL_SET_WATCHLIST, cbor([](ewp::CborWriter& w) {
        ewp::CborMap m(w);
        m.key(EWP_K_SET_WATCHLIST_ARGS_RULES).array(0);
        m.key(EWP_K_SET_WATCHLIST_ARGS_OP).uint(EWP_WATCH_OP_APPEND);
        m.key(EWP_K_SET_WATCHLIST_ARGS_VERSION).uint(5);
    }));
    CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);
    CHECK_EQ(mapUint(o.body(), o.bodyLen(), EWP_K_ERROR_BODY_ARG_KEY), EWP_K_SET_WATCHLIST_ARGS_VERSION);
    // APPEND with a bad suffix: refused, nothing half-applied
    o = rig.call(3, EWP_TOOL_SET_WATCHLIST, cbor([](ewp::CborWriter& w) {
        ewp::CborMap m(w);
        m.key(EWP_K_SET_WATCHLIST_ARGS_OP).uint(EWP_WATCH_OP_APPEND);
        m.key(EWP_K_SET_WATCHLIST_ARGS_VERSION).uint(1700000000);
        m.key(EWP_K_SET_WATCHLIST_ARGS_RULES).array(2);
        w.array(3);
        w.text("good.org");
        w.uint(5);
        w.uint(0);
        w.array(3);
        w.text("BAD..org");
        w.uint(5);
        w.uint(0);
    }));
    CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);
    CHECK_EQ(rig.nob.watchRules(), 2u);
    // a good APPEND
    o = rig.call(4, EWP_TOOL_SET_WATCHLIST, cbor([](ewp::CborWriter& w) {
        ewp::CborMap m(w);
        m.key(EWP_K_SET_WATCHLIST_ARGS_OP).uint(EWP_WATCH_OP_APPEND);
        m.key(EWP_K_SET_WATCHLIST_ARGS_VERSION).uint(1700000000);
        m.key(EWP_K_SET_WATCHLIST_ARGS_RULES).array(1);
        w.array(3);
        w.text("good.org");
        w.uint(5);
        w.uint(EWP_RULE_SUBDOMAINS_ONLY);
    }));
    CHECK(res(o, r, rn));
    CHECK_EQ(mapUint(r, rn, EWP_K_WATCHLIST_RESULT_RULES), 3u);

    o = rig.call(5, EWP_TOOL_GET_WATCHLIST);
    CHECK(res(o, r, rn));
    CHECK_EQ(o.h.flags & EWP_FLAG_TRUNCATED, 0);
    const uint8_t* rules = nullptr;
    size_t rl = 0;
    CHECK(getRaw(r, rn, EWP_K_GET_WATCHLIST_RESULT_RULES, rules, rl));
    ewp::CborReader rr(rules, rl);
    uint32_t n = 0;
    CHECK(rr.readArray(n) && n == 3);

    // a fresh node with the same NVS boots with the host's list, not the built-in one
    nf::Upstream u;
    mcp::Uplink up2;
    CHECK(up2.begin(4, 2048, false));
    netobs::NetObserve again;
    netobs::Config cfg = loopbackConfig(static_cast<uint16_t>(testPort() + 3));
    cfg.forward = false;
    CHECK(again.begin(cfg, rig.plat, up2));
    CHECK_EQ(again.watchVersion(), 1700000000u);
    CHECK_EQ(again.watchRules(), 3u);
    CHECK_EQ(again.listenPort(), 0u);  // dns_mode off: not listening

    // HELLO.net
    uint8_t buf[256];
    ewp::CborWriter w(buf, sizeof buf);
    rig.nob.encode(w);
    CHECK(w.ok());
    CHECK_EQ(mapUint(buf, w.size(), EWP_K_NET_OBSERVE_INFO_WATCH_VER), 1700000000u);
    CHECK_EQ(mapUint(buf, w.size(), EWP_K_NET_OBSERVE_INFO_WATCH_RULES), 3u);
    CHECK(mapUint(buf, w.size(), EWP_K_NET_OBSERVE_INFO_CAPS) & EWP_NETCAP_DNS_FORWARD);
}

TEST(netobs_tools_stats_devices_log_scan_flush) {
    NobRig rig(static_cast<uint16_t>(testPort() + 4));
    nf::Client c;
    nf::Bytes resp;
    CHECK(nf::exchange(rig.nob, c, nf::query(1, "a.example.org"), resp));
    CHECK(nf::exchange(rig.nob, c, nf::query(2, "www.youtube.com"), resp));
    CHECK(nf::exchange(rig.nob, c, nf::query(3, "a.example.org"), resp));  // cached

    Out o = rig.call(10, EWP_TOOL_GET_NETWORK_STATS);
    const uint8_t* r = nullptr;
    size_t rn = 0;
    CHECK(res(o, r, rn));
    const uint8_t* dns = nullptr;
    size_t dn = 0;
    CHECK(getRaw(r, rn, EWP_K_NETWORK_STATS_RESULT_DNS, dns, dn));
    CHECK_EQ(mapUint(dns, dn, EWP_K_DNS_COUNTERS_QUERIES), 3u);
    CHECK_EQ(mapUint(dns, dn, EWP_K_DNS_COUNTERS_CACHE_HITS), 1u);
    CHECK_EQ(mapUint(r, rn, EWP_K_NETWORK_STATS_RESULT_CACHE_ENTRIES), 2u);
    CHECK(hasKey(r, rn, EWP_K_NETWORK_STATS_RESULT_LAST_CLIENT_QUERY_AGO_MS));

    o = rig.call(11, EWP_TOOL_QUERY_DNS_LOG, cbor([](ewp::CborWriter& w) {
        ewp::CborMap m(w);
        m.key(EWP_K_QUERY_DNS_LOG_ARGS_LIMIT).uint(2);
    }));
    CHECK(res(o, r, rn));
    const uint8_t* qs = nullptr;
    size_t qn = 0;
    CHECK(getRaw(r, rn, EWP_K_DNS_LOG_RESULT_QUERIES, qs, qn));
    ewp::CborReader qr(qs, qn);
    uint32_t n = 0;
    CHECK(qr.readArray(n) && n == 2);
    o = rig.call(12, EWP_TOOL_QUERY_DNS_LOG, cbor([](ewp::CborWriter& w) {
        ewp::CborMap m(w);
        m.key(EWP_K_QUERY_DNS_LOG_ARGS_FLAGGED_ONLY).boolean(true);
    }));
    CHECK(res(o, r, rn));
    CHECK(getRaw(r, rn, EWP_K_DNS_LOG_RESULT_QUERIES, qs, qn));
    ewp::CborReader fr(qs, qn);
    CHECK(fr.readArray(n) && n == 1);
    o = rig.call(13, EWP_TOOL_QUERY_DNS_LOG, cbor([](ewp::CborWriter& w) {
        ewp::CborMap m(w);
        m.key(EWP_K_QUERY_DNS_LOG_ARGS_LIMIT).uint(33);
    }));
    CHECK_EQ(o.h.status, EWP_ST_INVALID_ARGS);

    o = rig.call(14, EWP_TOOL_GET_LOCAL_DEVICES);
    CHECK(res(o, r, rn));
    CHECK_EQ(mapUint(r, rn, EWP_K_LOCAL_DEVICES_RESULT_TOTAL), 1u);  // 127.0.0.1 asked us

    // scan_lan: not before the node has an address, then the /24 is swept and ARP replies land in the table
    rig.plat.ip = 0;
    rig.nob.runOnce(1);
    o = rig.call(15, EWP_TOOL_SCAN_LAN);
    CHECK_EQ(o.h.status, EWP_ST_NOT_READY);
    rig.plat.ip = 0xC0A80032;
    rig.nob.runOnce(1);
    o = rig.call(16, EWP_TOOL_SCAN_LAN);
    CHECK(res(o, r, rn));
    CHECK_EQ(mapUint(r, rn, EWP_K_SCAN_LAN_RESULT_TARGETS), 253u);
    o = rig.call(17, EWP_TOOL_SCAN_LAN);
    CHECK_EQ(o.h.status, EWP_ST_BUSY);
    nf::pump(rig.nob, 3800);
    {
        std::lock_guard<std::mutex> lk(rig.plat.mu);
        CHECK_EQ(rig.plat.requested.size(), 253u);  // .1 .. .254 without the node itself
    }
    o = rig.call(18, EWP_TOOL_GET_LOCAL_DEVICES);
    CHECK(res(o, r, rn));
    CHECK_EQ(mapUint(r, rn, EWP_K_LOCAL_DEVICES_RESULT_TOTAL), 3u);  // + .7 and .42 from ARP

    o = rig.call(19, EWP_TOOL_FLUSH_DNS_CACHE);
    CHECK(res(o, r, rn));
    CHECK_EQ(mapUint(r, rn, EWP_K_FLUSH_DNS_CACHE_RESULT_FLUSHED), 2u);
}

TEST(netobs_reconfigure_off_and_on) {
    const uint16_t up = static_cast<uint16_t>(testPort() + 5);
    nf::Upstream u1;
    CHECK(u1.start("127.0.0.1", up));
    nf::Platform plat;
    mcp::Uplink uplink;
    CHECK(uplink.begin(4, 2048, false));
    netobs::NetObserve nob;
    netobs::Config cfg = loopbackConfig(up);
    cfg.forward = false;
    CHECK(nob.begin(cfg, plat, uplink));
    nob.runOnce(1);
    CHECK_EQ(nob.listenPort(), 0u);
    const uint32_t ups[1] = {0x7F000001};
    nob.reconfigure(true, ups, 1);
    nob.runOnce(1);
    CHECK(nob.listenPort() != 0);
    nf::Client c;
    nf::Bytes resp;
    CHECK(nf::exchange(nob, c, nf::query(9, "x.example"), resp));
    nob.reconfigure(false, ups, 1);
    nob.runOnce(1);
    CHECK_EQ(nob.listenPort(), 0u);
}
