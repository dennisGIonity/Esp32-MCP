/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/NetObserve.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The forwarder loop, discovery sockets and timers. BSD sockets only: lwIP on
 * the device, the host's sockets in test/host.
 */
#include "netobserve/NetObserve.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#include <initializer_list>
#include <new>

#include "netobserve/Discovery.h"
#include "tools/Identity.h"

namespace netobs {
namespace {

constexpr uint32_t kMdnsGroup = 0xE00000FBu;  // 224.0.0.251
constexpr int kReadBurst = 16;               // datagrams per socket per wake-up

bool isPrivate(uint32_t ip) {
    const uint32_t a = ip >> 24;
    const uint32_t b = (ip >> 16) & 0xFF;
    return a == 10 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168) || (a == 169 && b == 254) ||
           (a == 100 && b >= 64 && b <= 127);  // RFC 1918, link-local, CGNAT (some ISP routers)
}

uint16_t sat16(int64_t v) { return v < 0 ? 0 : v > 0xFFFF ? 0xFFFF : static_cast<uint16_t>(v); }

}  // namespace

NetObserve::~NetObserve() {
    closeSockets();
    os::freePool(active_);
    os::freePool(staging_);
    os::freePool(blob_);
    os::freePool(pending_);
    os::freePool(pendingPool_);
    os::freePool(pkt_);
    os::freePool(pkt2_);
}

uint32_t NetObserve::nowMs() const { return static_cast<uint32_t>(os::nowUs() / 1000); }

bool NetObserve::begin(const Config& cfg, Platform& platform, mcp::Uplink& uplink) {
    cfg_ = cfg;
    if (cfg_.upstreamCount == 0 || cfg_.upstreamCount > 2) return false;
    platform_ = &platform;
    uplink_ = &uplink;
    if (!mu_.begin(false)) return false;

    void* a = os::allocPool(sizeof(Watchlist), cfg.preferPsram);
    void* s = os::allocPool(sizeof(Watchlist), cfg.preferPsram);
    blob_ = static_cast<uint8_t*>(os::allocPool(Watchlist::kBlobMax, cfg.preferPsram));
    pending_ = static_cast<Pending*>(os::allocPool(sizeof(Pending) * cfg.pendingSlots, false));
    pendingPool_ = static_cast<uint8_t*>(os::allocPool(kPendingQueryMax * cfg.pendingSlots, cfg.preferPsram));
    pkt_ = static_cast<uint8_t*>(os::allocPool(kPacketMax, false));
    pkt2_ = static_cast<uint8_t*>(os::allocPool(kPacketMax, false));
    if (a == nullptr || s == nullptr || blob_ == nullptr || pending_ == nullptr || pendingPool_ == nullptr ||
        pkt_ == nullptr || pkt2_ == nullptr) {
        return false;
    }
    active_ = new (a) Watchlist();
    staging_ = new (s) Watchlist();
    for (uint16_t i = 0; i < cfg.pendingSlots; ++i) {
        pending_[i] = Pending{};
        pending_[i].query = pendingPool_ + static_cast<size_t>(i) * kPendingQueryMax;
    }
    if (!cache_.begin(cfg.cache) || !log_.begin(cfg.logBytes, cfg.preferPsram)) return false;

    // The host's last list survives reboots; a missing or damaged blob means the built-in list.
    size_t len = 0;
    if (!(platform.loadWatchlist(blob_, Watchlist::kBlobMax, len) && active_->deserialize(blob_, len))) {
        active_->loadDefaults();
    }

    wantForward_ = cfg.forward;
    memcpy(wantUpstreams_, cfg.upstreams, sizeof wantUpstreams_);
    wantUpstreamCount_ = cfg.upstreamCount;
    c_.mode = EWP_DNS_MODE_OFF;
    const int64_t now = os::nowUs();
    nextBatchUs_ = now + static_cast<int64_t>(cfg.batchPeriodMs) * 1000;
    lastBatchUs_ = now;
    // Sockets are opened by the loop (runOnce): at boot lwIP may not be running yet.
    return true;
}

/* ---- sockets ------------------------------------------------------------------ */

int NetObserve::openUdp(uint16_t port, bool loopback, bool reuse) {
    const int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (fd < 0) return -1;
    const int one = 1;
    if (reuse) setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &one, sizeof one);
    sockaddr_in sa;
    memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET;
    sa.sin_port = htons(port);
    sa.sin_addr.s_addr = htonl(loopback ? INADDR_LOOPBACK : INADDR_ANY);
    if (bind(fd, reinterpret_cast<sockaddr*>(&sa), sizeof sa) != 0) {
        close(fd);
        return -1;
    }
    const int fl = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, fl | O_NONBLOCK);
    return fd;
}

void NetObserve::joinMdns() {
    if (mdnsSock_ < 0) return;
    ip_mreq mr;
    memset(&mr, 0, sizeof mr);
    mr.imr_multiaddr.s_addr = htonl(kMdnsGroup);
    mr.imr_interface.s_addr = htonl(INADDR_ANY);
    setsockopt(mdnsSock_, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mr, sizeof mr);  // repeated after every new address
}

void NetObserve::openSockets() {
    if (!cfg_.forward) return;
    const int64_t now = os::nowUs();
    if (now < nextBindRetryUs_) return;
    nextBindRetryUs_ = now + 5000000;  // a failed bind is retried every 5 s, never in a tight loop
    if (dnsSock_ < 0) {
        dnsSock_ = openUdp(cfg_.port, cfg_.loopbackOnly, true);
        if (dnsSock_ >= 0) {
            sockaddr_in sa;
            socklen_t sl = sizeof sa;
            if (getsockname(dnsSock_, reinterpret_cast<sockaddr*>(&sa), &sl) == 0) {
                listenPort_.store(ntohs(sa.sin_port), std::memory_order_relaxed);
            }
        }
    }
    if (upSock_ < 0) upSock_ = openUdp(0, false, false);
    if (cfg_.mdnsPort != 0 && mdnsSock_ < 0) {
        mdnsSock_ = openUdp(cfg_.mdnsPort, false, true);
        joinMdns();
    }
    if (cfg_.dhcpPort != 0 && dhcpSock_ < 0) dhcpSock_ = openUdp(cfg_.dhcpPort, false, true);
    os::Lock lk(mu_);
    c_.mode = (dnsSock_ >= 0 && upSock_ >= 0) ? EWP_DNS_MODE_FORWARD : EWP_DNS_MODE_OFF;
}

void NetObserve::closeSockets() {
    for (int* fd : {&dnsSock_, &upSock_, &mdnsSock_, &dhcpSock_}) {
        if (*fd >= 0) close(*fd);
        *fd = -1;
    }
    listenPort_.store(0, std::memory_order_relaxed);
    // Queries in flight can no longer be answered: forget them.
    for (uint16_t i = 0; pending_ != nullptr && i < cfg_.pendingSlots; ++i) pending_[i].used = false;
    pendingCount_.store(0, std::memory_order_relaxed);
}

void NetObserve::stop() { closeSockets(); }

void NetObserve::reconfigure(bool forward, const uint32_t* upstreams, uint8_t count) {
    {
        os::Lock lk(mu_);
        wantForward_ = forward;
        if (count >= 1 && count <= 2) {
            wantUpstreams_[0] = upstreams[0];
            wantUpstreams_[1] = count > 1 ? upstreams[1] : 0;
            wantUpstreamCount_ = count;
        }
    }
    reconfig_.store(true, std::memory_order_release);
}

void NetObserve::sendTo(int sock, const uint8_t* p, size_t n, uint32_t addrNet, uint16_t portNet) {
    sockaddr_in sa;
    memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET;
    sa.sin_port = portNet;
    sa.sin_addr.s_addr = addrNet;
    sendto(sock, p, n, MSG_DONTWAIT, reinterpret_cast<sockaddr*>(&sa), sizeof sa);
}

bool NetObserve::clientAllowed(uint32_t ip) const {
    if ((ip >> 24) == 127) return true;  // the node itself / host tests
    if (ip_ != 0 && mask_ != 0 && (ip & mask_) == (ip_ & mask_)) return true;
    return isPrivate(ip);  // never an open resolver for the internet
}

/* ---- the loop ------------------------------------------------------------------- */

bool NetObserve::startTask(unsigned priority, int core, uint32_t stackBytes) {
    return os::spawn("netobs", &NetObserve::taskEntry, this, stackBytes, priority, core, nullptr);
}

void NetObserve::taskEntry(void* self) {
    NetObserve* n = static_cast<NetObserve*>(self);
    os::wdtSubscribe();
    for (;;) {
        os::wdtReset();
        n->runOnce(100);
    }
}

void NetObserve::runOnce(uint32_t maxWaitMs) {
    if (reconfig_.exchange(false, std::memory_order_acq_rel)) {
        bool forward;
        {
            os::Lock lk(mu_);
            forward = wantForward_;
            memcpy(cfg_.upstreams, wantUpstreams_, sizeof cfg_.upstreams);
            cfg_.upstreamCount = wantUpstreamCount_;
            up_[0] = UpstreamStats{};
            up_[1] = UpstreamStats{};
        }
        if (forward != cfg_.forward) {
            cfg_.forward = forward;
            closeSockets();
            nextBindRetryUs_ = 0;
            os::Lock lk(mu_);
            c_.mode = EWP_DNS_MODE_OFF;
        }
        nextResolversUs_ = 0;
    }

    // The station address: a new one re-joins the mDNS group and re-points our own resolver.
    uint32_t ip = 0;
    uint32_t mask = 0;
    if (!platform_->stationIp(ip, mask)) ip = mask = 0;
    if (ip != ip_) {
        ip_ = ip;
        mask_ = mask;
        ipShared_.store(ip, std::memory_order_relaxed);
        if (ip != 0) joinMdns();
        nextResolversUs_ = 0;
    }
    // DHCP renewals put the router's DNS (this node) back: re-point our own resolver every minute.
    if (ip_ != 0 && os::nowUs() >= nextResolversUs_) {
        platform_->useResolvers(cfg_.upstreams, cfg_.upstreamCount);
        nextResolversUs_ = os::nowUs() + 60000000;
    }
    if (cfg_.forward && (dnsSock_ < 0 || upSock_ < 0 || (cfg_.mdnsPort && mdnsSock_ < 0) || (cfg_.dhcpPort && dhcpSock_ < 0))) {
        openSockets();
    }

    fd_set rd;
    FD_ZERO(&rd);
    int maxFd = -1;
    for (int fd : {dnsSock_, upSock_, mdnsSock_, dhcpSock_}) {
        if (fd >= 0) {
            FD_SET(fd, &rd);
            if (fd > maxFd) maxFd = fd;
        }
    }
    const uint32_t waitMs = maxWaitMs > 100 ? 100 : maxWaitMs;  // timers need a 100 ms tick
    timeval tv;
    tv.tv_sec = 0;
    tv.tv_usec = static_cast<long>(waitMs) * 1000;
    int ready = 0;
    if (maxFd >= 0) {
        ready = select(maxFd + 1, &rd, nullptr, nullptr, &tv);
    } else {
        os::delayMs(waitMs);
    }
    const int64_t t0 = os::nowUs();
    if (ready > 0) {
        sockaddr_in from;
        socklen_t fl;
        if (dnsSock_ >= 0 && FD_ISSET(dnsSock_, &rd)) {
            for (int i = 0; i < kReadBurst; ++i) {
                fl = sizeof from;
                const ssize_t n = recvfrom(dnsSock_, pkt_, kPacketMax, MSG_DONTWAIT, reinterpret_cast<sockaddr*>(&from), &fl);
                if (n <= 0) break;
                onQuery(pkt_, static_cast<size_t>(n), from.sin_addr.s_addr, from.sin_port);
            }
        }
        if (upSock_ >= 0 && FD_ISSET(upSock_, &rd)) {
            for (int i = 0; i < kReadBurst; ++i) {
                fl = sizeof from;
                const ssize_t n = recvfrom(upSock_, pkt_, kPacketMax, MSG_DONTWAIT, reinterpret_cast<sockaddr*>(&from), &fl);
                if (n <= 0) break;
                if (ntohs(from.sin_port) == cfg_.upstreamPort) onUpstream(pkt_, static_cast<size_t>(n), ntohl(from.sin_addr.s_addr));
            }
        }
        if (mdnsSock_ >= 0 && FD_ISSET(mdnsSock_, &rd)) {
            for (int i = 0; i < kReadBurst; ++i) {
                fl = sizeof from;
                const ssize_t n = recvfrom(mdnsSock_, pkt_, kPacketMax, MSG_DONTWAIT, reinterpret_cast<sockaddr*>(&from), &fl);
                if (n <= 0) break;
                onMdns(pkt_, static_cast<size_t>(n), ntohl(from.sin_addr.s_addr));
            }
        }
        if (dhcpSock_ >= 0 && FD_ISSET(dhcpSock_, &rd)) {
            for (int i = 0; i < kReadBurst; ++i) {
                fl = sizeof from;
                const ssize_t n = recvfrom(dhcpSock_, pkt_, kPacketMax, MSG_DONTWAIT, reinterpret_cast<sockaddr*>(&from), &fl);
                if (n <= 0) break;
                onDhcp(pkt_, static_cast<size_t>(n));
            }
        }
    }
    service(os::nowUs());
    const int64_t dt = os::nowUs() - t0;
    const uint32_t us = dt > 0xFFFFFFFF ? 0xFFFFFFFFu : static_cast<uint32_t>(dt);
    if (us > runUsMax_.load(std::memory_order_relaxed)) runUsMax_.store(us, std::memory_order_relaxed);
}

/* ---- packet paths --------------------------------------------------------------- */

void NetObserve::onQuery(const uint8_t* p, size_t n, uint32_t addrNet, uint16_t portNet) {
    const uint32_t ip = ntohl(addrNet);
    static DnsQuestion q;  // netobs task only (253-byte name: keep it off the stack)
    if (!clientAllowed(ip) || !parseQuery(p, n, q)) {
        os::Lock lk(mu_);
        ++c_.dropped;
        return;
    }
    const int64_t now = os::nowUs();
    const bool self = ip_ != 0 && ip == ip_;  // our own resolver: forwarded, not reported
    Watchlist::Match m;
    size_t hit = 0;
    {
        os::Lock lk(mu_);
        ++c_.queries;
        if (!self) {
            clients_.touch(ip, nowMs(), EWP_DISC_DNS);
            lastClientQueryUs_ = now;
        }
        active_->match(q.name, q.nameLen, m);
        if (m.category != 0) ++c_.flagged;
        if (q.qclass == kClassIn) hit = cache_.lookup(p, q, now, pkt2_, kPacketMax);
        if (hit > 0) ++c_.cacheHits;
    }
    if (hit > 0) {
        sendTo(dnsSock_, pkt2_, hit, addrNet, portNet);
        const uint8_t flags = static_cast<uint8_t>(EWP_DNSQ_CACHED | (m.category ? EWP_DNSQ_FLAGGED : 0));
        if (!self) record(ip, q, static_cast<uint8_t>(pkt2_[3] & 0x0F), 0, flags, m.category);
        if (m.flags & EWP_RULE_ALERT) maybeFlag(ip, q, m);
        return;
    }
    Pending* e = nullptr;
    for (uint16_t i = 0; i < cfg_.pendingSlots; ++i) {
        if (!pending_[i].used) {
            e = &pending_[i];
            break;
        }
    }
    if (e == nullptr || n > kPendingQueryMax) {
        // Overload (or an oversized query): fail fast rather than make the client wait.
        const size_t len = buildServfail(p, q, pkt2_, kPacketMax);
        if (len > 0) sendTo(dnsSock_, pkt2_, len, addrNet, portNet);
        {
            os::Lock lk(mu_);
            ++c_.dropped;
        }
        if (!self) record(ip, q, 2, 0, static_cast<uint8_t>(EWP_DNSQ_UPSTREAM_FAILED | (m.category ? EWP_DNSQ_FLAGGED : 0)), m.category);
        return;
    }
    // A fresh upstream id per question: the client's id is restored on the way back, and a
    // forged reply would have to guess both the id and our source port.
    uint16_t upId = 0;
    for (int tries = 0; tries < 8; ++tries) {
        upId = static_cast<uint16_t>(os::random32());
        bool clash = false;
        for (uint16_t i = 0; i < cfg_.pendingSlots; ++i) clash |= pending_[i].used && pending_[i].upId == upId;
        if (!clash) break;
    }
    uint8_t* const qbuf = e->query;
    *e = Pending{};
    e->query = qbuf;
    e->used = true;
    e->category = m.category;
    e->qflags = static_cast<uint8_t>(m.category ? EWP_DNSQ_FLAGGED : 0);
    e->upId = upId;
    e->clientId = rd16(p);
    e->clientPort = portNet;
    e->clientAddr = addrNet;
    e->clientIp = self ? 0 : ip;  // 0 = our own resolver: not reported
    e->t0Us = now;
    e->retryUs = now + static_cast<int64_t>(cfg_.retryMs) * 1000;
    e->failUs = now + static_cast<int64_t>(cfg_.failMs) * 1000;
    e->qLen = static_cast<uint16_t>(n);
    memcpy(e->query, p, n);
    wr16(e->query, upId);
    pendingCount_.fetch_add(1, std::memory_order_relaxed);
    if (!sendUpstream(*e, 0)) e->retryUs = now;  // try the other upstream on the next tick
    {
        os::Lock lk(mu_);
        ++c_.forwarded;
    }
    if (m.flags & EWP_RULE_ALERT) maybeFlag(ip, q, m);
}

bool NetObserve::sendUpstream(Pending& e, uint8_t idx) {
    if (upSock_ < 0 || idx >= cfg_.upstreamCount) return false;
    e.upstream = idx;
    ++e.attempts;
    sockaddr_in sa;
    memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET;
    sa.sin_port = htons(cfg_.upstreamPort);
    sa.sin_addr.s_addr = htonl(cfg_.upstreams[idx]);
    return sendto(upSock_, e.query, e.qLen, MSG_DONTWAIT, reinterpret_cast<sockaddr*>(&sa), sizeof sa) ==
           static_cast<ssize_t>(e.qLen);
}

void NetObserve::onUpstream(const uint8_t* p, size_t n, uint32_t fromIp) {
    if (n < kDnsHeaderLen) return;
    int which = -1;
    for (uint8_t i = 0; i < cfg_.upstreamCount; ++i) {
        if (cfg_.upstreams[i] == fromIp) which = i;
    }
    if (which < 0) return;  // not from a resolver we asked
    const uint16_t id = rd16(p);
    Pending* e = nullptr;
    for (uint16_t i = 0; i < cfg_.pendingSlots; ++i) {
        if (pending_[i].used && pending_[i].upId == id) e = &pending_[i];
    }
    if (e == nullptr) return;  // late duplicate (the other upstream won) or a guess
    static DnsQuestion q;      // netobs task only
    DnsAnswerInfo info{};
    if (!parseQuery(e->query, e->qLen, q) || !parseResponse(p, n, q, info)) return;  // not an answer to OUR question
    const int64_t now = os::nowUs();
    uint8_t* out = pkt2_;
    memcpy(out, p, n);
    wr16(out, e->clientId);  // the id the client chose
    sendTo(dnsSock_, out, n, e->clientAddr, e->clientPort);
    const uint16_t latency = sat16((now - e->t0Us) / 1000);
    {
        os::Lock lk(mu_);
        cache_.store(q, p, n, info, now);
        up_[which].ok++;
        up_[which].latencySumMs += latency;
    }
    uint8_t flags = e->qflags;
    if (info.truncated) flags |= EWP_DNSQ_TRUNCATED;
    if (e->clientIp != 0) record(e->clientIp, q, info.rcode, latency, flags, e->category);
    e->used = false;
    pendingCount_.fetch_sub(1, std::memory_order_relaxed);
}

void NetObserve::onMdns(const uint8_t* p, size_t n, uint32_t fromIp) {
    struct Ctx {
        NetObserve* self;
        uint32_t now;
    } ctx{this, nowMs()};
    os::Lock lk(mu_);
    if (fromIp != 0 && fromIp != ip_ && isPrivate(fromIp)) clients_.touch(fromIp, ctx.now, EWP_DISC_MDNS);
    parseMdns(
        p, n,
        [](void* u, uint32_t ip, const char* name, size_t len) {
            Ctx* c = static_cast<Ctx*>(u);
            if (ip != c->self->ip_ && isPrivate(ip)) c->self->clients_.setHostname(ip, name, len, c->now, EWP_DISC_MDNS);
        },
        &ctx);
}

void NetObserve::onDhcp(const uint8_t* p, size_t n) {
    DhcpInfo d;
    if (!parseDhcpRequest(p, n, d)) return;
    os::Lock lk(mu_);
    clients_.dhcp(d.mac, d.ip, d.hostname, d.hostnameLen, nowMs());
}

/* ---- records and events -------------------------------------------------------- */

void NetObserve::record(uint32_t ip, const DnsQuestion& q, uint8_t rcode, uint16_t latencyMs, uint8_t flags,
                        uint8_t category) {
    static QueryRecord r;  // netobs task only
    r.tMs = nowMs();
    r.ip = ip;
    r.qtype = q.qtype;
    r.latencyMs = latencyMs;
    r.rcode = rcode;
    r.flags = flags;
    r.category = category;
    r.nameLen = q.nameLen;
    memcpy(r.name, q.name, q.nameLen);
    r.name[q.nameLen] = '\0';
    os::Lock lk(mu_);
    log_.push(r);
}

void NetObserve::maybeFlag(uint32_t ip, const DnsQuestion& q, const Watchlist::Match& m) {
    platform_->alert(m.category, cfg_.alertHoldMs);  // the light follows every hit
    const uint32_t now = nowMs();
    FlagMemo* slot = nullptr;
    FlagMemo* oldest = &flags_[0];
    for (FlagMemo& f : flags_) {
        if (f.ip == ip && f.rule == m.index) slot = &f;
        if (now - f.atMs > now - oldest->atMs) oldest = &f;
    }
    if (slot != nullptr && slot->ip != 0 && now - slot->atMs < cfg_.flagRepeatMs) return;  // reported recently
    if (slot == nullptr) slot = oldest;
    if (!emitFlag(ip, q, m)) return;  // not sent: the next hit tries again
    *slot = FlagMemo{ip, static_cast<int16_t>(m.index), now};
}

bool NetObserve::emitFlag(uint32_t ip, const DnsQuestion& q, const Watchlist::Match& m) {
    if (!platform_->linkUp()) {
        ++flagsDropped_;
        return false;
    }
    const int slot = uplink_->acquire(0);
    if (slot < 0) {
        uplink_->noteDrop();
        return false;
    }
    uint8_t mac[6];
    const bool hasMac = platform_->arpLookup(ip, mac);
    char host[EWP_LIMIT_HOSTNAME_BYTES + 1] = {};
    char suffix[Watchlist::kSuffixMax + 1] = {};
    uint32_t ver = 0;
    {
        os::Lock lk(mu_);
        if (hasMac) clients_.setMac(ip, mac, nowMs(), EWP_DISC_ARP);
        const LanDevice* d = clients_.find(ip);
        if (d != nullptr) memcpy(host, d->hostname, sizeof host);
        if (m.index >= 0 && m.index < active_->ruleCount()) memcpy(suffix, active_->suffix(static_cast<size_t>(m.index)), active_->rule(static_cast<size_t>(m.index)).len);
        ver = active_->version();
    }
    const uint8_t ipb[4] = {static_cast<uint8_t>(ip >> 24), static_cast<uint8_t>(ip >> 16), static_cast<uint8_t>(ip >> 8),
                            static_cast<uint8_t>(ip)};
    const size_t len = tools::buildDeviceFrame(uplink_->buffer(slot), uplink_->slotSize(), EWP_MSG_EVENT,
                                               rid_.fetch_add(1, std::memory_order_relaxed), [&](ewp::CborWriter& w) {
        ewp::CborMap ev(w);
        ev.key(EWP_K_EVENT_BODY_KIND).uint(EWP_EVENT_DNS_FLAGGED);
        ewp::CborMap f(ev.key(EWP_K_EVENT_BODY_FLAG));
        ewp::CborWriter& cw = f.key(EWP_K_DNS_FLAG_CLIENT);
        cw.array(3);
        cw.bytes(ipb, 4);
        cw.bytes(mac, hasMac ? 6 : 0);
        cw.text(host);
        f.key(EWP_K_DNS_FLAG_QNAME).text(q.name, q.nameLen);
        f.key(EWP_K_DNS_FLAG_QTYPE).uint(q.qtype);
        f.key(EWP_K_DNS_FLAG_CATEGORY).uint(m.category);
        f.key(EWP_K_DNS_FLAG_RULE).text(suffix);
        f.key(EWP_K_DNS_FLAG_AGE_MS).uint(0);
        f.key(EWP_K_DNS_FLAG_WATCH_VER).uint(ver);
    });
    if (len == 0) {
        uplink_->release(slot);
        uplink_->noteDrop();
        return false;
    }
    return uplink_->submit(slot, len, 0, 0);
}

/* ---- timers -------------------------------------------------------------------- */

void NetObserve::service(int64_t nowUs) {
    servicePending(nowUs);
    serviceScan(nowUs);
    pollArp(nowUs);
    flushBatches(nowUs);
}

void NetObserve::servicePending(int64_t nowUs) {
    for (uint16_t i = 0; i < cfg_.pendingSlots; ++i) {
        Pending& e = pending_[i];
        if (!e.used) continue;
        if (nowUs >= e.failUs) {
            static DnsQuestion q;
            if (parseQuery(e.query, e.qLen, q)) {
                // SERVFAIL with the client's own id.
                const size_t len = buildServfail(e.query, q, pkt2_, kPacketMax);
                if (len > 0) {
                    wr16(pkt2_, e.clientId);
                    sendTo(dnsSock_, pkt2_, len, e.clientAddr, e.clientPort);
                }
                if (e.clientIp != 0) {
                    record(e.clientIp, q, 2, sat16((nowUs - e.t0Us) / 1000),
                           static_cast<uint8_t>(e.qflags | EWP_DNSQ_UPSTREAM_FAILED), e.category);
                }
            }
            os::Lock lk(mu_);
            ++c_.upstreamFail;
            up_[e.upstream < 2 ? e.upstream : 0].fail++;
            e.used = false;
            pendingCount_.fetch_sub(1, std::memory_order_relaxed);
        } else if (nowUs >= e.retryUs) {
            e.retryUs = e.failUs;  // one retry
            const uint8_t next = cfg_.upstreamCount > 1 ? static_cast<uint8_t>((e.upstream + 1) % cfg_.upstreamCount) : e.upstream;
            {
                os::Lock lk(mu_);
                up_[e.upstream < 2 ? e.upstream : 0].fail++;
            }
            sendUpstream(e, next);
        }
    }
}

void NetObserve::serviceScan(int64_t nowUs) {
    uint32_t batch[8];
    size_t n = 0;
    {
        os::Lock lk(mu_);
        if (!scanActive_ || nowUs < scanNextUs_) return;
        while (n < 8 && scanNext_ <= 254) {
            const uint32_t target = scanBase_ | scanNext_++;
            if (target != ip_) batch[n++] = target;
        }
        scanNextUs_ = nowUs + 100000;  // 8 requests per 100 ms: ~3 s for a /24, gentle on the AP
        if (scanNext_ > 254 && nowUs >= scanEndUs_) scanActive_ = false;
    }
    if (n > 0) platform_->arpRequest(batch, n);
}

void NetObserve::pollArp(int64_t nowUs) {
    bool scanning;
    {
        os::Lock lk(mu_);
        scanning = scanActive_;
    }
    if (!cfg_.forward && !scanning) return;  // dns_mode off: only an explicit scan reads the table
    if (scanning && nextArpUs_ - nowUs > 250000) nextArpUs_ = nowUs;  // a scan just started: read replies now
    if (nowUs < nextArpUs_) return;
    nextArpUs_ = nowUs + (scanning ? 250000 : static_cast<int64_t>(cfg_.arpPollMs) * 1000);
    static ArpEntry table[32];  // lwIP ARP_TABLE_SIZE is 10; read often during a scan
    const size_t n = platform_->arpTable(table, 32);
    const uint32_t now = nowMs();
    os::Lock lk(mu_);
    for (size_t i = 0; i < n; ++i) {
        if (table[i].ip != 0 && table[i].ip != ip_ && isPrivate(table[i].ip)) {
            clients_.setMac(table[i].ip, table[i].mac, now, EWP_DISC_ARP);
        }
    }
}

void NetObserve::flushBatches(int64_t nowUs) {
    bool due;
    {
        os::Lock lk(mu_);
        if (log_.unsent() == 0) {
            if (nowUs >= nextBatchUs_) {
                nextBatchUs_ = nowUs + static_cast<int64_t>(cfg_.batchPeriodMs) * 1000;
                lastBatchUs_ = nowUs;
            }
            return;
        }
        due = nowUs >= nextBatchUs_ || log_.unsentFillPct() >= cfg_.earlyFlushPct;
    }
    if (!due || !platform_->linkUp()) return;  // offline: the ring keeps them (oldest lost when full)
    ++batchSeq_;
    for (int frames = 0; frames < 8; ++frames) {  // a burst of up to 8 frames per flush
        const int slot = uplink_->acquire(0);
        if (slot < 0) {
            uplink_->noteDrop();
            break;
        }
        bool more = false;
        const size_t len = buildBatch(uplink_->buffer(slot), uplink_->slotSize(), more);
        if (len == 0) {
            uplink_->release(slot);
            break;
        }
        if (!uplink_->submit(slot, len, 0, 0)) break;
        if (!more) break;
    }
    nextBatchUs_ = nowUs + static_cast<int64_t>(cfg_.batchPeriodMs) * 1000;
    lastBatchUs_ = nowUs;
}

}  // namespace netobs
