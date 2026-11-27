/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/NetTools.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * What NetObserve says on the wire: DNS_BATCH frames, HELLO.net,
 * TELEMETRY.dns and the NetObserve tools (0x40-0x47). Bodies follow
 * packages/protocol/src/netobserve.ts key for key.
 *
 *   0x40 set_watchlist     {1 op, 2 version, 3 rules[[suffix,cat,flags]], 4 categories?[[id,name]]}
 *                          -> {1 version, 2 rules, 3 categories}            idempotent
 *   0x41 get_watchlist     {} -> {1 version, 2 rules[], 3 categories[]}    read-only (TRUNCATED if trimmed)
 *   0x42 get_network_stats {} -> {1 dns, 2 upstreams[[ip,ok,fail,meanMs]], 3 cacheEntries, 4 pending, 5 lastClientQueryAgoMs?}
 *   0x43 get_local_devices {1 limit?} -> {1 devices[[ip,mac,host,ageS,queries,sources]], 2 total}
 *   0x44 query_dns_log     {1 limit?, 2 client?, 3 flaggedOnly?} -> {1 clients[], 2 queries[]} newest first
 *   0x45 scan_lan          {} -> {1 targets, 2 etaMs}
 *   0x46 flush_dns_cache   {} -> {1 flushed}
 *   0x47 ping_host         {1 target, 2 count?, 3 timeoutMs?, 4 intervalMs?}
 *                          -> {1 error, 2 resolvedIp?, 3 sent, 4 received, 5-7 rtt min/avg/max ms?}
 *                          long-running: waits on the worker, polls cancel/deadline, feeds the WDT
 */
#include <string.h>

#include "netobserve/NetObserve.h"
#include "tools/Args.h"

namespace netobs {
namespace {

void ipBytes(uint32_t ip, uint8_t out[4]) {
    out[0] = static_cast<uint8_t>(ip >> 24);
    out[1] = static_cast<uint8_t>(ip >> 16);
    out[2] = static_cast<uint8_t>(ip >> 8);
    out[3] = static_cast<uint8_t>(ip);
}

/** Bytes a CBOR head for value v takes. */
size_t headLen(uint64_t v) { return v < 24 ? 1 : v <= 0xFF ? 2 : v <= 0xFFFF ? 3 : v <= 0xFFFFFFFFu ? 5 : 9; }

void encodeClient(ewp::CborWriter& w, uint32_t ip, const uint8_t* mac, bool hasMac, const char* host) {
    uint8_t b[4];
    ipBytes(ip, b);
    w.array(3);
    w.bytes(b, 4);
    w.bytes(mac, hasMac ? 6 : 0);
    w.text(host, strnlen(host, EWP_LIMIT_HOSTNAME_BYTES));
}

/** Upper bound of one DnsQuery tuple. */
size_t queryCost(uint8_t nameLen) { return 1 + 5 + 2 + headLen(nameLen) + nameLen + 3 + 2 + 3 + 3 + 2 + 2; }
/** Upper bound of one DnsClient tuple. */
size_t clientCost(size_t hostLen) { return 1 + 5 + 7 + headLen(hostLen) + hostLen; }

}  // namespace

/* ---- DNS_BATCH ------------------------------------------------------------------ */

size_t NetObserve::buildBatch(uint8_t* buf, size_t cap, bool& more) {
    struct Fold {
        uint32_t tMs;  // newest occurrence
        uint32_t ip;
        uint16_t qtype;
        uint16_t latency;
        uint16_t count;
        uint16_t nameOff;
        uint8_t nameLen;
        uint8_t client;
        uint8_t rcode;
        uint8_t flags;
        uint8_t category;
    };
    struct Cl {
        uint32_t ip;
        uint8_t mac[6];
        bool hasMac;
        char host[EWP_LIMIT_HOSTNAME_BYTES + 1];
    };
    static constexpr size_t kFolds = 96;
    static constexpr size_t kClients = 32;
    // netobs task only (the one caller): kept off its stack.
    static Fold folds[kFolds];
    static Cl cls[kClients];
    static char names[2048];
    static QueryRecord r;

    more = false;
    if (cap <= EWP_HEADER_LEN + 64) return 0;
    const size_t budget = cap - EWP_HEADER_LEN - 64;  // 64: the map heads, seq, window, dropped, more
    os::Lock lk(mu_);
    const uint32_t now = nowMs();
    size_t used = 0;
    size_t nf = 0;
    size_t nc = 0;
    size_t namesUsed = 0;
    const uint32_t start = log_.sent() > log_.oldestSeq() ? log_.sent() : log_.oldestSeq();
    const uint32_t end = log_.nextSeq();
    uint32_t consumed = start;
    for (uint32_t seq = start; seq < end; ++seq) {
        if (!log_.get(seq, r)) {
            consumed = seq + 1;
            continue;
        }
        size_t f = 0;
        for (; f < nf; ++f) {
            const Fold& x = folds[f];
            if (x.ip == r.ip && x.qtype == r.qtype && x.nameLen == r.nameLen && memcmp(names + x.nameOff, r.name, r.nameLen) == 0) break;
        }
        if (f < nf) {  // the same question again: one entry, newest time, a count
            Fold& x = folds[f];
            x.tMs = r.tMs;
            x.latency = r.latencyMs;
            x.rcode = r.rcode;
            x.flags |= r.flags;
            if (r.category != 0) x.category = r.category;
            if (x.count < 0xFFFF) ++x.count;
            consumed = seq + 1;
            continue;
        }
        size_t c = 0;
        for (; c < nc; ++c) {
            if (cls[c].ip == r.ip) break;
        }
        size_t cost = queryCost(r.nameLen);
        const LanDevice* dev = clients_.find(r.ip);
        if (c == nc) {
            if (nc == kClients) break;
            cost += clientCost(dev != nullptr ? strnlen(dev->hostname, EWP_LIMIT_HOSTNAME_BYTES) : 0);
        }
        if (nf == kFolds || used + cost > budget || namesUsed + r.nameLen > sizeof names) break;
        if (c == nc) {
            Cl& n = cls[nc++];
            n = Cl{};
            n.ip = r.ip;
            if (dev != nullptr && dev->hasMac) {
                memcpy(n.mac, dev->mac, 6);
                n.hasMac = true;
            } else if (platform_->arpLookup(r.ip, n.mac)) {
                n.hasMac = true;
                clients_.setMac(r.ip, n.mac, now, EWP_DISC_ARP);
            }
            if (dev != nullptr) memcpy(n.host, dev->hostname, sizeof n.host);
        }
        Fold& x = folds[nf++];
        x = Fold{r.tMs, r.ip, r.qtype, r.latencyMs, 1, static_cast<uint16_t>(namesUsed), r.nameLen,
                 static_cast<uint8_t>(c), r.rcode, r.flags, r.category};
        memcpy(names + namesUsed, r.name, r.nameLen);
        namesUsed += r.nameLen;
        used += cost;
        consumed = seq + 1;
    }
    if (nf == 0) {
        log_.markSent(consumed);
        return 0;
    }
    more = consumed < end;
    const uint32_t lost = log_.takeLost();
    const int64_t nowUs = os::nowUs();
    const uint32_t windowMs = static_cast<uint32_t>((nowUs - lastBatchUs_) / 1000);
    const size_t len = tools::buildDeviceFrame(buf, cap, EWP_MSG_EVENT, rid_.fetch_add(1, std::memory_order_relaxed),
                                               [&](ewp::CborWriter& w) {
        ewp::CborMap ev(w);
        ev.key(EWP_K_EVENT_BODY_KIND).uint(EWP_EVENT_DNS_BATCH);
        ewp::CborMap b(ev.key(EWP_K_EVENT_BODY_DNS));
        b.key(EWP_K_DNS_BATCH_SEQ).uint(batchSeq_);
        b.key(EWP_K_DNS_BATCH_WINDOW_MS).uint(windowMs);
        b.key(EWP_K_DNS_BATCH_CLIENTS).array(static_cast<uint32_t>(nc));
        for (size_t i = 0; i < nc; ++i) encodeClient(w, cls[i].ip, cls[i].mac, cls[i].hasMac, cls[i].host);
        b.key(EWP_K_DNS_BATCH_QUERIES).array(static_cast<uint32_t>(nf));
        for (size_t i = 0; i < nf; ++i) {
            const Fold& x = folds[i];
            w.array(9);
            w.uint(now - x.tMs);  // ageMs
            w.uint(x.client);
            w.text(names + x.nameOff, x.nameLen);
            w.uint(x.qtype);
            w.uint(x.rcode);
            w.uint(x.latency);
            w.uint(x.count);
            w.uint(x.flags);
            w.uint(x.category);
        }
        if (lost > 0) b.key(EWP_K_DNS_BATCH_DROPPED).uint(lost);
        if (more) b.key(EWP_K_DNS_BATCH_MORE).boolean(true);
    });
    if (len > 0) log_.markSent(consumed);
    return len;
}

/* ---- HELLO.net / TELEMETRY.dns ----------------------------------------------------- */

void NetObserve::encode(ewp::CborWriter& w) const {
    os::Lock lk(mu_);
    ewp::CborMap m(w);
    m.key(EWP_K_NET_OBSERVE_INFO_MODE).uint(c_.mode);
    m.key(EWP_K_NET_OBSERVE_INFO_PORT).uint(cfg_.port);
    m.key(EWP_K_NET_OBSERVE_INFO_UPSTREAMS).array(wantUpstreamCount_);
    for (uint8_t i = 0; i < wantUpstreamCount_ && i < 2; ++i) {
        uint8_t b[4];
        ipBytes(wantUpstreams_[i], b);
        w.bytes(b, 4);
    }
    m.key(EWP_K_NET_OBSERVE_INFO_CAPS)
        .uint(EWP_NETCAP_DNS_FORWARD | EWP_NETCAP_MDNS_LISTEN | EWP_NETCAP_DHCP_SNIFF | EWP_NETCAP_ARP_SCAN |
              EWP_NETCAP_DNS_CACHE);
    m.key(EWP_K_NET_OBSERVE_INFO_WATCH_VER).uint(active_->version());
    m.key(EWP_K_NET_OBSERVE_INFO_WATCH_RULES).uint(active_->ruleCount());
}

void NetObserve::counters(tools::DnsCountersSnap& out) const {
    os::Lock lk(mu_);
    out = c_;
    const uint32_t now = nowMs();
    out.clients = clients_.activeClients(now, 600000);
    out.lanDevices = static_cast<uint16_t>(clients_.size());
}

uint32_t NetObserve::watchVersion() const {
    os::Lock lk(mu_);
    return active_->version();
}

uint16_t NetObserve::watchRules() const {
    os::Lock lk(mu_);
    return active_->ruleCount();
}

/* ---- tools ----------------------------------------------------------------------- */

bool NetObserve::registerTools(mcp::MCPServer& s) {
    constexpr uint8_t RO = EWP_TOOL_FLAG_READ_ONLY;
    constexpr uint8_t ID = EWP_TOOL_FLAG_IDEMPOTENT;
    bool ok = s.registerTool({EWP_TOOL_SET_WATCHLIST, 1, ID, EWP_TOOL_NAME_SET_WATCHLIST, &NetObserve::toolSetWatchlist, this});
    ok = ok && s.registerTool({EWP_TOOL_GET_WATCHLIST, 1, RO | ID, EWP_TOOL_NAME_GET_WATCHLIST, &NetObserve::toolGetWatchlist, this});
    ok = ok && s.registerTool({EWP_TOOL_GET_NETWORK_STATS, 1, RO, EWP_TOOL_NAME_GET_NETWORK_STATS, &NetObserve::toolNetworkStats, this});
    ok = ok && s.registerTool({EWP_TOOL_GET_LOCAL_DEVICES, 1, RO, EWP_TOOL_NAME_GET_LOCAL_DEVICES, &NetObserve::toolLocalDevices, this});
    ok = ok && s.registerTool({EWP_TOOL_QUERY_DNS_LOG, 1, RO, EWP_TOOL_NAME_QUERY_DNS_LOG, &NetObserve::toolQueryLog, this});
    ok = ok && s.registerTool({EWP_TOOL_SCAN_LAN, 1, 0, EWP_TOOL_NAME_SCAN_LAN, &NetObserve::toolScanLan, this});
    ok = ok && s.registerTool({EWP_TOOL_FLUSH_DNS_CACHE, 1, ID, EWP_TOOL_NAME_FLUSH_DNS_CACHE, &NetObserve::toolFlushCache, this});
    ok = ok && s.registerTool({EWP_TOOL_PING_HOST, 1, RO | EWP_TOOL_FLAG_LONG_RUNNING, EWP_TOOL_NAME_PING_HOST,
                               &NetObserve::toolPingHost, this});
    return ok;
}

void NetObserve::persistWatchlist() {
    // blob_ is used by the MCP worker only (tools run one at a time) and at boot.
    size_t len;
    {
        os::Lock lk(mu_);
        len = active_->serialize(blob_, Watchlist::kBlobMax);
    }
    if (len > 0) platform_->saveWatchlist(blob_, len);
}

void NetObserve::toolSetWatchlist(mcp::ToolContext& ctx) {
    NetObserve* self = static_cast<NetObserve*>(ctx.user());
    uint64_t op = 0;
    uint64_t ver = 0;
    bool haveOp = false;
    bool haveVer = false;
    bool haveRules = false;
    // Pass 1: op + version (CBOR maps have no key order).
    bool ok = tools::parseArgs(ctx, [&](uint64_t key, ewp::CborReader& r) {
        switch (key) {
            case EWP_K_SET_WATCHLIST_ARGS_OP: return (haveOp = r.readUint(op) && op <= EWP_WATCH_OP_APPEND);
            case EWP_K_SET_WATCHLIST_ARGS_VERSION: return (haveVer = r.readUint(ver) && ver <= 0xFFFFFFFFu);
            case EWP_K_SET_WATCHLIST_ARGS_RULES: haveRules = true; return r.skip();
            default: return tools::skipUnknownArg(r);
        }
    });
    if (!ok) return;
    if (!haveOp) return ctx.fail(EWP_ST_INVALID_ARGS, "op is required (0 REPLACE, 1 APPEND)", EWP_K_SET_WATCHLIST_ARGS_OP);
    if (!haveVer) return ctx.fail(EWP_ST_INVALID_ARGS, "version is required", EWP_K_SET_WATCHLIST_ARGS_VERSION);
    if (!haveRules) return ctx.fail(EWP_ST_INVALID_ARGS, "rules is required (may be empty)", EWP_K_SET_WATCHLIST_ARGS_RULES);

    uint32_t version;
    uint16_t rules;
    uint8_t cats;
    {
        os::Lock lk(self->mu_);
        Watchlist& st = *self->staging_;
        if (op == EWP_WATCH_OP_REPLACE) {
            st.clear(static_cast<uint32_t>(ver));
        } else {
            if (static_cast<uint32_t>(ver) != self->active_->version()) {
                return ctx.fail(EWP_ST_INVALID_ARGS, "APPEND must carry the version of the list it extends (send REPLACE first)",
                                EWP_K_SET_WATCHLIST_ARGS_VERSION);
            }
            st = *self->active_;
        }
        // Pass 2: rules and categories into the staged copy; any error leaves the active list alone.
        ok = tools::parseArgs(ctx, [&](uint64_t key, ewp::CborReader& r) {
            const char* err = "malformed";
            if (key == EWP_K_SET_WATCHLIST_ARGS_RULES) {
                uint32_t n = 0;
                if (!r.readArray(n)) return false;
                for (uint32_t i = 0; i < n; ++i) {
                    uint32_t three = 0;
                    const char* s = nullptr;
                    size_t len = 0;
                    uint64_t cat = 0;
                    uint64_t flags = 0;
                    if (!r.readArray(three) || three != 3 || !r.readText(s, len) || !r.readUint(cat) || cat > 0xFF ||
                        !r.readUint(flags) || flags > 0xFF) {
                        ctx.fail(EWP_ST_INVALID_ARGS, "a rule is [suffix, category, flags]", EWP_K_SET_WATCHLIST_ARGS_RULES);
                        return false;
                    }
                    if (!st.upsertRule(s, len, static_cast<uint8_t>(cat), static_cast<uint8_t>(flags), &err)) {
                        ctx.fail(EWP_ST_INVALID_ARGS, err, EWP_K_SET_WATCHLIST_ARGS_RULES);
                        return false;
                    }
                }
                return true;
            }
            if (key == EWP_K_SET_WATCHLIST_ARGS_CATEGORIES) {
                uint32_t n = 0;
                if (!r.readArray(n)) return false;
                for (uint32_t i = 0; i < n; ++i) {
                    uint32_t two = 0;
                    uint64_t id = 0;
                    const char* s = nullptr;
                    size_t len = 0;
                    if (!r.readArray(two) || two != 2 || !r.readUint(id) || id > 0xFF || !r.readText(s, len)) {
                        ctx.fail(EWP_ST_INVALID_ARGS, "a category is [id, name]", EWP_K_SET_WATCHLIST_ARGS_CATEGORIES);
                        return false;
                    }
                    if (!st.upsertCategory(static_cast<uint8_t>(id), s, len, &err)) {
                        ctx.fail(EWP_ST_INVALID_ARGS, err, EWP_K_SET_WATCHLIST_ARGS_CATEGORIES);
                        return false;
                    }
                }
                return true;
            }
            return tools::skipUnknownArg(r);
        });
        if (!ok) return;
        *self->active_ = st;
        version = st.version();
        rules = st.ruleCount();
        cats = st.categoryCount();
    }
    self->persistWatchlist();  // NVS write outside the lock: the forwarder keeps answering
    ewp::CborMap m(ctx.out());
    m.key(EWP_K_WATCHLIST_RESULT_VERSION).uint(version);
    m.key(EWP_K_WATCHLIST_RESULT_RULES).uint(rules);
    m.key(EWP_K_WATCHLIST_RESULT_CATEGORIES).uint(cats);
}

void NetObserve::toolGetWatchlist(mcp::ToolContext& ctx) {
    NetObserve* self = static_cast<NetObserve*>(ctx.user());
    if (!tools::parseArgs(ctx, [](uint64_t, ewp::CborReader& r) { return tools::skipUnknownArg(r); })) return;
    os::Lock lk(self->mu_);
    const Watchlist& wl = *self->active_;
    ewp::CborWriter& w = ctx.out();
    size_t catBytes = 4;
    for (size_t i = 0; i < wl.categoryCount(); ++i) catBytes += 4 + strlen(wl.categoryName(i));
    const size_t room = w.remaining() > 16 + catBytes ? w.remaining() - 16 - catBytes : 0;
    size_t fit = 0;
    size_t used = 3;
    for (; fit < wl.ruleCount(); ++fit) {
        const size_t cost = 1 + headLen(wl.rule(fit).len) + wl.rule(fit).len + 2 + 2;
        if (used + cost > room) break;
        used += cost;
    }
    ewp::CborMap m(w);
    m.key(EWP_K_GET_WATCHLIST_RESULT_VERSION).uint(wl.version());
    m.key(EWP_K_GET_WATCHLIST_RESULT_RULES).array(static_cast<uint32_t>(fit));
    for (size_t i = 0; i < fit; ++i) {
        w.array(3);
        w.text(wl.suffix(i), wl.rule(i).len);
        w.uint(wl.rule(i).category);
        w.uint(wl.rule(i).flags);
    }
    m.key(EWP_K_GET_WATCHLIST_RESULT_CATEGORIES).array(wl.categoryCount());
    for (size_t i = 0; i < wl.categoryCount(); ++i) {
        w.array(2);
        w.uint(wl.categoryId(i));
        w.text(wl.categoryName(i));
    }
    if (fit < wl.ruleCount()) ctx.setTruncated();
}

void NetObserve::toolNetworkStats(mcp::ToolContext& ctx) {
    NetObserve* self = static_cast<NetObserve*>(ctx.user());
    if (!tools::parseArgs(ctx, [](uint64_t, ewp::CborReader& r) { return tools::skipUnknownArg(r); })) return;
    tools::DnsCountersSnap c;
    self->counters(c);
    const int64_t now = os::nowUs();
    os::Lock lk(self->mu_);
    ewp::CborWriter& w = ctx.out();
    ewp::CborMap m(w);
    tools::encodeDnsCounters(m.key(EWP_K_NETWORK_STATS_RESULT_DNS), c);
    m.key(EWP_K_NETWORK_STATS_RESULT_UPSTREAMS).array(self->wantUpstreamCount_);
    for (uint8_t i = 0; i < self->wantUpstreamCount_ && i < 2; ++i) {
        const UpstreamStats& u = self->up_[i];
        uint8_t b[4];
        ipBytes(self->wantUpstreams_[i], b);
        w.array(4);
        w.bytes(b, 4);
        w.uint(u.ok);
        w.uint(u.fail);
        const uint64_t mean = u.ok > 0 ? u.latencySumMs / u.ok : 0;
        w.uint(mean > 0xFFFF ? 0xFFFF : mean);
    }
    m.key(EWP_K_NETWORK_STATS_RESULT_CACHE_ENTRIES).uint(self->cache_.live(now));
    m.key(EWP_K_NETWORK_STATS_RESULT_PENDING).uint(self->pendingCount_.load(std::memory_order_relaxed));
    if (self->lastClientQueryUs_ != 0) {
        const int64_t ago = (now - self->lastClientQueryUs_) / 1000;
        m.key(EWP_K_NETWORK_STATS_RESULT_LAST_CLIENT_QUERY_AGO_MS).uint(ago > 0xFFFFFFFF ? 0xFFFFFFFFu : static_cast<uint64_t>(ago));
    }
}

void NetObserve::toolLocalDevices(mcp::ToolContext& ctx) {
    NetObserve* self = static_cast<NetObserve*>(ctx.user());
    uint64_t limit = EWP_LIMIT_LOCAL_DEVICES;
    const bool ok = tools::parseArgs(ctx, [&](uint64_t key, ewp::CborReader& r) {
        if (key == EWP_K_GET_LOCAL_DEVICES_ARGS_LIMIT) {
            if (!r.readUint(limit) || limit == 0 || limit > EWP_LIMIT_LOCAL_DEVICES) {
                ctx.fail(EWP_ST_INVALID_ARGS, "limit must be 1-128", EWP_K_GET_LOCAL_DEVICES_ARGS_LIMIT);
                return false;
            }
            return true;
        }
        return tools::skipUnknownArg(r);
    });
    if (!ok) return;
    os::Lock lk(self->mu_);
    const uint32_t now = self->nowMs();
    const size_t total = self->clients_.size();
    const size_t want = total < limit ? total : static_cast<size_t>(limit);
    ewp::CborWriter& w = ctx.out();
    const size_t room = w.remaining() > 24 ? w.remaining() - 24 : 0;
    size_t fit = 0;
    size_t used = 3;
    for (; fit < want; ++fit) {
        const LanDevice* d = self->clients_.byRecency(fit, now);
        if (d == nullptr) break;
        const size_t cost = 1 + 5 + 7 + 2 + strnlen(d->hostname, EWP_LIMIT_HOSTNAME_BYTES) + 5 + 5 + 2;
        if (used + cost > room) break;
        used += cost;
    }
    ewp::CborMap m(w);
    m.key(EWP_K_LOCAL_DEVICES_RESULT_DEVICES).array(static_cast<uint32_t>(fit));
    for (size_t i = 0; i < fit; ++i) {
        const LanDevice* d = self->clients_.byRecency(i, now);
        uint8_t b[4];
        ipBytes(d->ip, b);
        w.array(6);
        w.bytes(b, 4);
        w.bytes(d->mac, d->hasMac ? 6 : 0);
        w.text(d->hostname, strnlen(d->hostname, EWP_LIMIT_HOSTNAME_BYTES));
        w.uint((now - d->lastSeenMs) / 1000);
        w.uint(d->queries);
        w.uint(d->sources);
    }
    m.key(EWP_K_LOCAL_DEVICES_RESULT_TOTAL).uint(total);
    if (fit < want) ctx.setTruncated();
}

void NetObserve::toolQueryLog(mcp::ToolContext& ctx) {
    NetObserve* self = static_cast<NetObserve*>(ctx.user());
    uint64_t limit = 16;
    bool flaggedOnly = false;
    bool haveClient = false;
    uint32_t client = 0;
    const bool ok = tools::parseArgs(ctx, [&](uint64_t key, ewp::CborReader& r) {
        switch (key) {
            case EWP_K_QUERY_DNS_LOG_ARGS_LIMIT:
                if (!r.readUint(limit) || limit == 0 || limit > EWP_LIMIT_DNS_LOG_QUERY) {
                    ctx.fail(EWP_ST_INVALID_ARGS, "limit must be 1-32", EWP_K_QUERY_DNS_LOG_ARGS_LIMIT);
                    return false;
                }
                return true;
            case EWP_K_QUERY_DNS_LOG_ARGS_CLIENT: {
                const uint8_t* b = nullptr;
                size_t n = 0;
                if (!r.readBytes(b, n) || n != 4) {
                    ctx.fail(EWP_ST_INVALID_ARGS, "client is a 4-byte IPv4 address", EWP_K_QUERY_DNS_LOG_ARGS_CLIENT);
                    return false;
                }
                client = (static_cast<uint32_t>(b[0]) << 24) | (static_cast<uint32_t>(b[1]) << 16) |
                         (static_cast<uint32_t>(b[2]) << 8) | b[3];
                haveClient = true;
                return true;
            }
            case EWP_K_QUERY_DNS_LOG_ARGS_FLAGGED_ONLY: return r.readBool(flaggedOnly);
            default: return tools::skipUnknownArg(r);
        }
    });
    if (!ok) return;
    static uint32_t picked[EWP_LIMIT_DNS_LOG_QUERY];  // worker only
    static uint32_t ips[EWP_LIMIT_DNS_LOG_QUERY];
    static QueryRecord r;
    os::Lock lk(self->mu_);
    const uint32_t now = self->nowMs();
    size_t n = 0;
    size_t nIps = 0;
    ewp::CborWriter& w = ctx.out();
    const size_t room = w.remaining() > 24 ? w.remaining() - 24 : 0;
    size_t used = 4;
    bool trimmed = false;
    for (size_t i = 0; i < self->log_.count() && n < limit; ++i) {
        if (!self->log_.newest(i, r)) break;
        if (haveClient && r.ip != client) continue;
        if (flaggedOnly && (r.flags & EWP_DNSQ_FLAGGED) == 0) continue;
        size_t c = 0;
        while (c < nIps && ips[c] != r.ip) ++c;
        size_t cost = queryCost(r.nameLen) + (c == nIps ? clientCost(EWP_LIMIT_HOSTNAME_BYTES) : 0);
        if (used + cost > room) {
            trimmed = true;
            break;
        }
        if (c == nIps) ips[nIps++] = r.ip;
        picked[n++] = r.seq;
        used += cost;
    }
    ewp::CborMap m(w);
    m.key(EWP_K_DNS_LOG_RESULT_CLIENTS).array(static_cast<uint32_t>(nIps));
    for (size_t c = 0; c < nIps; ++c) {
        const LanDevice* d = self->clients_.find(ips[c]);
        static const uint8_t kNoMac[6] = {};
        encodeClient(w, ips[c], d != nullptr ? d->mac : kNoMac, d != nullptr && d->hasMac, d != nullptr ? d->hostname : "");
    }
    m.key(EWP_K_DNS_LOG_RESULT_QUERIES).array(static_cast<uint32_t>(n));
    for (size_t i = 0; i < n; ++i) {
        self->log_.get(picked[i], r);
        size_t c = 0;
        while (c < nIps && ips[c] != r.ip) ++c;
        w.array(9);
        w.uint(now - r.tMs);
        w.uint(c);
        w.text(r.name, r.nameLen);
        w.uint(r.qtype);
        w.uint(r.rcode);
        w.uint(r.latencyMs);
        w.uint(1);
        w.uint(r.flags);
        w.uint(r.category);
    }
    if (trimmed) ctx.setTruncated();
}

void NetObserve::toolScanLan(mcp::ToolContext& ctx) {
    NetObserve* self = static_cast<NetObserve*>(ctx.user());
    if (!tools::parseArgs(ctx, [](uint64_t, ewp::CborReader& r) { return tools::skipUnknownArg(r); })) return;
    const uint32_t ip = self->ipShared_.load(std::memory_order_relaxed);
    if (ip == 0) return ctx.fail(EWP_ST_NOT_READY, "no IPv4 address yet - the node is not on the Wi-Fi", -1, 5000);
    const int64_t now = os::nowUs();
    os::Lock lk(self->mu_);
    if (self->scanActive_) {
        const int64_t left = (self->scanEndUs_ - now) / 1000;
        return ctx.fail(EWP_ST_BUSY, "a LAN scan is already running", -1, left > 0 ? static_cast<uint32_t>(left) : 500);
    }
    // The node's /24 (a wider netmask is still swept as the node's own /24: 253 targets).
    self->scanBase_ = ip & 0xFFFFFF00u;
    self->scanNext_ = 1;
    self->scanNextUs_ = now;
    const uint32_t etaMs = (253 / 8 + 1) * 100 + 2000;  // requests paced 8 / 100 ms, then 2 s for late replies
    self->scanEndUs_ = now + static_cast<int64_t>(etaMs) * 1000;
    self->scanActive_ = true;
    ewp::CborMap m(ctx.out());
    m.key(EWP_K_SCAN_LAN_RESULT_TARGETS).uint(253);
    m.key(EWP_K_SCAN_LAN_RESULT_ETA_MS).uint(etaMs);
}

void NetObserve::toolFlushCache(mcp::ToolContext& ctx) {
    NetObserve* self = static_cast<NetObserve*>(ctx.user());
    if (!tools::parseArgs(ctx, [](uint64_t, ewp::CborReader& r) { return tools::skipUnknownArg(r); })) return;
    uint16_t n;
    {
        os::Lock lk(self->mu_);
        n = self->cache_.flush(os::nowUs());
    }
    ewp::CborMap m(ctx.out());
    m.key(EWP_K_FLUSH_DNS_CACHE_RESULT_FLUSHED).uint(n);
}

/* ---- 0x47 ping_host --------------------------------------------------------------- */

namespace {

/** RFC 1123 host name or dotted IPv4: letters, digits, '-', '.'; labels 1-63, no leading/trailing '-'. */
bool validTarget(const char* s, size_t n) {
    if (n == 0 || n > EWP_LIMIT_QNAME_BYTES) return false;
    size_t label = 0;
    for (size_t i = 0; i < n; ++i) {
        const char c = s[i];
        if (c == '.') {
            if (label == 0 || s[i - 1] == '-') return false;
            label = 0;
            continue;
        }
        const bool alnum = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
        if (!alnum && !(c == '-' && label > 0)) return false;
        if (++label > 63) return false;
    }
    return s[n - 1] != '-';
}

void encodePing(ewp::CborWriter& w, uint8_t error, bool haveIp, uint32_t ip, const PingStats& st) {
    ewp::CborMap m(w);
    m.key(EWP_K_PING_HOST_RESULT_ERROR).uint(error);
    if (haveIp) {
        uint8_t b[4];
        ipBytes(ip, b);
        m.key(EWP_K_PING_HOST_RESULT_RESOLVED_IP).bytes(b, 4);
    }
    m.key(EWP_K_PING_HOST_RESULT_SENT).uint(st.sent);
    m.key(EWP_K_PING_HOST_RESULT_RECEIVED).uint(st.received);
    if (st.received > 0) {
        const auto clamp = [](uint32_t v) -> uint64_t { return v > 0xFFFFu ? 0xFFFFu : v; };
        m.key(EWP_K_PING_HOST_RESULT_RTT_MIN_MS).uint(clamp(st.rttMinMs));
        m.key(EWP_K_PING_HOST_RESULT_RTT_AVG_MS).uint(clamp((st.rttSumMs + st.received / 2u) / st.received));  // rounded, like the Pi agent
        m.key(EWP_K_PING_HOST_RESULT_RTT_MAX_MS).uint(clamp(st.rttMaxMs));
    }
}

}  // namespace

void NetObserve::toolPingHost(mcp::ToolContext& ctx) {
    NetObserve* self = static_cast<NetObserve*>(ctx.user());
    char target[EWP_LIMIT_QNAME_BYTES + 1] = {};
    bool haveTarget = false;
    uint64_t count = 4;
    uint64_t timeoutMs = 1000;
    uint64_t intervalMs = 1000;
    const bool ok = tools::parseArgs(ctx, [&](uint64_t key, ewp::CborReader& r) {
        switch (key) {
            case EWP_K_PING_HOST_ARGS_TARGET: {
                const char* s = nullptr;
                size_t n = 0;
                if (!r.readText(s, n) || !validTarget(s, n)) {
                    ctx.fail(EWP_ST_INVALID_ARGS, "target must be a host name or IPv4 address", EWP_K_PING_HOST_ARGS_TARGET);
                    return false;
                }
                memcpy(target, s, n);
                target[n] = '\0';
                return (haveTarget = true);
            }
            case EWP_K_PING_HOST_ARGS_COUNT:
                if (!r.readUint(count) || count < 1 || count > EWP_LIMIT_PING_COUNT_MAX) {
                    ctx.fail(EWP_ST_INVALID_ARGS, "count must be 1-10", EWP_K_PING_HOST_ARGS_COUNT);
                    return false;
                }
                return true;
            case EWP_K_PING_HOST_ARGS_TIMEOUT_MS:
                if (!r.readUint(timeoutMs) || timeoutMs < EWP_LIMIT_PING_TIMEOUT_MIN_MS || timeoutMs > EWP_LIMIT_PING_TIMEOUT_MAX_MS) {
                    ctx.fail(EWP_ST_INVALID_ARGS, "timeoutMs must be 100-5000", EWP_K_PING_HOST_ARGS_TIMEOUT_MS);
                    return false;
                }
                return true;
            case EWP_K_PING_HOST_ARGS_INTERVAL_MS:
                if (!r.readUint(intervalMs) || intervalMs < EWP_LIMIT_PING_INTERVAL_MIN_MS ||
                    intervalMs > EWP_LIMIT_PING_INTERVAL_MAX_MS) {
                    ctx.fail(EWP_ST_INVALID_ARGS, "intervalMs must be 200-5000", EWP_K_PING_HOST_ARGS_INTERVAL_MS);
                    return false;
                }
                return true;
            default: return tools::skipUnknownArg(r);
        }
    });
    if (!ok) return;
    if (!haveTarget) return ctx.fail(EWP_ST_INVALID_ARGS, "target is required", EWP_K_PING_HOST_ARGS_TARGET);

    // The whole session must fit the request deadline, or the result could never be sent.
    const int64_t needUs = static_cast<int64_t>((count - 1) * intervalMs + timeoutMs + 250) * 1000;
    if (needUs > ctx.remainingUs()) {
        return ctx.fail(EWP_ST_INVALID_ARGS, "count x intervalMs + timeoutMs exceeds the request deadline",
                        EWP_K_PING_HOST_ARGS_COUNT);
    }

    PingStats st;
    uint32_t ip = 0;
    if (self->pingBusy_.exchange(true, std::memory_order_acq_rel)) {
        encodePing(ctx.out(), EWP_PING_BUSY, false, 0, st);
        return;
    }
    struct Release {
        NetObserve* s;
        ~Release() {
            s->platform_->pingStop();
            s->pingBusy_.store(false, std::memory_order_release);
        }
    } release{self};

    if (!self->platform_->resolveHost(target, ip) || ip == 0) {
        encodePing(ctx.out(), EWP_PING_RESOLVE_FAILED, false, 0, st);
        return;
    }
    if (!self->platform_->pingStart(ip, static_cast<uint8_t>(count), static_cast<uint16_t>(timeoutMs),
                                    static_cast<uint16_t>(intervalMs))) {
        encodePing(ctx.out(), EWP_PING_NOT_PERMITTED, true, ip, st);
        return;
    }
    for (;;) {
        self->platform_->pingStatus(st);
        if (st.done) break;
        if (ctx.cancelled()) return ctx.fail(EWP_ST_CANCELLED, "cancelled");
        if (ctx.remainingUs() < 150000) break;  // answer with what we have rather than miss the deadline
        ctx.keepAlive();
        os::delayMs(20);
    }
    encodePing(ctx.out(), st.received > 0 ? EWP_PING_NONE : EWP_PING_TIMEOUT, true, ip, st);
}

}  // namespace netobs
