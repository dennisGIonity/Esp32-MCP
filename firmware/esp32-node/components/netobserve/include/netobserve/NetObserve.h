/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/NetObserve.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * NetObserve (PLAN-v3): the node as the LAN's DNS server. When the router's
 * DHCP hands out this board's (reserved) address as the DNS server, every
 * device on the LAN asks the board; the board forwards each question to the
 * provisioned upstream resolvers and reports WHO asked for WHAT - metadata
 * only, never an answer address or a payload.
 *
 *            LAN clients                        upstreams (1.1.1.1, 8.8.8.8)
 *               |  UDP 53                               ^  UDP 53
 *   +-----------v---------------------------------------+-----------------+
 *   | netobs task (Core 0, select(), never blocks on one client)          |
 *   |  parse question -> watchlist match -> cache? -> pending[upId] ------+
 *   |  reply <- restore client id <- validate question <- upstream answer |
 *   |  log record (ring) . client table (ARP / DHCP / mDNS / DNS)         |
 *   |  every 10 s (or 75 % full): DNS_BATCH EVENT(s) -> Uplink -> net_task|
 *   |  ALERT rule hit: DNS_FLAGGED EVENT now + local alert output pulse    |
 *   +---------------------------------------------------------------------+
 *     tools on the MCP worker: set/get_watchlist, get_network_stats,
 *     get_local_devices, query_dns_log, scan_lan, flush_dns_cache
 *
 * The forwarder and parsers are portable (BSD sockets - lwIP on the device,
 * POSIX in the host tests); everything that touches ESP-IDF (ARP table, the
 * station address, NVS, the alert pin) sits behind `Platform`.
 *
 * NEVER a resolver loop: the upstreams are the provisioned ones (default
 * 1.1.1.1, 8.8.8.8), never the DHCP-learned server - on a LAN that points DHCP
 * DNS at this board that would be the board itself. The node's own lwIP
 * resolver is pointed at the upstreams too (Platform::useResolvers).
 *
 * MEMORY: every buffer is allocated once in begin() (PSRAM when present);
 * nothing on the packet path allocates.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <atomic>

#include "ewp/cbor.h"
#include "mcp/MCPServer.h"
#include "mcp/Uplink.h"
#include "mcp/os.h"
#include "netobserve/ClientTable.h"
#include "netobserve/DnsCache.h"
#include "netobserve/DnsWire.h"
#include "netobserve/QueryLog.h"
#include "netobserve/Watchlist.h"
#include "tools/Identity.h"
#include "tools/Telemetry.h"

namespace netobs {

struct ArpEntry {
    uint32_t ip;  // host order
    uint8_t mac[6];
};

/** Everything platform-specific. Calls come from the netobs task unless noted. */
class Platform {
public:
    /** The station address and netmask (host order); false while there is none. */
    virtual bool stationIp(uint32_t& ip, uint32_t& mask) = 0;
    /** MAC for an address from the ARP cache; false when not resolved. */
    virtual bool arpLookup(uint32_t ip, uint8_t mac[6]) = 0;
    /** Copy of the ARP cache. */
    virtual size_t arpTable(ArpEntry* out, size_t max) = 0;
    /** Send ARP requests (scan_lan); returns how many went out. */
    virtual size_t arpRequest(const uint32_t* ips, size_t n) = 0;
    /** Point the node's OWN resolver at these upstreams (it must never ask itself). */
    virtual void useResolvers(const uint32_t* ips, size_t n) = 0;
    /** The broker session is up (events have somewhere to go). Any task. */
    virtual bool linkUp() = 0;
    /** Local alert output for a flagged ALERT match (renewed on every hit). */
    virtual void alert(uint8_t category, uint32_t holdMs) = 0;
    /** Persisted watchlist blob (NVS). MCP worker / boot. */
    virtual bool loadWatchlist(uint8_t* buf, size_t cap, size_t& len) = 0;
    virtual bool saveWatchlist(const uint8_t* buf, size_t len) = 0;

protected:
    ~Platform() = default;
};

struct Config {
    /** dns_mode == "forward": listen on `port` and forward. Off: only scan_lan / ARP reads. */
    bool forward = false;
    uint16_t port = 53;
    uint32_t upstreams[2] = {0x01010101u, 0x08080808u};  // host order: 1.1.1.1, 8.8.8.8
    uint8_t upstreamCount = 2;
    uint16_t upstreamPort = 53;
    /** Bind the listener to 127.0.0.1 only (host tests). */
    bool loopbackOnly = false;

    uint32_t retryMs = 1500;     // second upstream after this
    uint32_t failMs = 3000;      // SERVFAIL after this
    uint16_t pendingSlots = 24;  // queries in flight upstream
    uint32_t batchPeriodMs = 10000;
    uint8_t earlyFlushPct = 75;
    uint32_t flagRepeatMs = 30000;  // one DNS_FLAGGED per client + rule per this window
    uint32_t alertHoldMs = 20000;   // local alert output on time after the last hit
    uint32_t arpPollMs = 10000;

    DnsCache::Config cache;
    size_t logBytes = 16384;
    bool preferPsram = true;

    /** Passive discovery (forward mode only). Port 0 = off. */
    uint16_t mdnsPort = 5353;
    uint16_t dhcpPort = 67;
};

struct UpstreamStats {
    uint32_t ok = 0;
    uint32_t fail = 0;
    uint64_t latencySumMs = 0;
};

class NetObserve final : public tools::HelloSection {
public:
    NetObserve() = default;
    ~NetObserve();
    NetObserve(const NetObserve&) = delete;
    NetObserve& operator=(const NetObserve&) = delete;

    /** Boot: buffers, watchlist (NVS or built-in), sockets. Call before registerTools/startTask. */
    bool begin(const Config& cfg, Platform& platform, mcp::Uplink& uplink);
    /** Registers 0x40..0x46 on the MCP server. Before server.begin(). */
    bool registerTools(mcp::MCPServer& server);
    /** Live change of dns_mode / dns_upstream (from provisioning). Any task. */
    void reconfigure(bool forward, const uint32_t* upstreams, uint8_t count);

    /** One loop iteration: wait <= maxWaitMs for packets, handle them, run timers. netobs task only. */
    void runOnce(uint32_t maxWaitMs);
    bool startTask(unsigned priority, int core, uint32_t stackBytes);
    /** Host tests: close the sockets. */
    void stop();

    /** UDP port the forwarder is bound to (0 = not listening). */
    uint16_t listenPort() const { return listenPort_.load(std::memory_order_relaxed); }
    bool forwarding() const { return listenPort() != 0; }

    /** TELEMETRY.dns (any task). */
    void counters(tools::DnsCountersSnap& out) const;
    uint32_t runUsMax() const { return runUsMax_.load(std::memory_order_relaxed); }

    // HelloSection: HELLO.net (NetObserveInfo)
    bool present() const override { return true; }
    void encode(ewp::CborWriter& w) const override;

    /* ---- for the tools and tests (lock held inside) ---------------------- */
    uint32_t watchVersion() const;
    uint16_t watchRules() const;

private:
    struct Pending {
        bool used;
        uint8_t upstream;  // index tried last
        uint8_t attempts;
        uint8_t category;
        uint8_t qflags;    // EWP_DNSQ_FLAGGED ...
        uint16_t upId;
        uint16_t clientId;    // the client's DNS id, restored on the reply
        uint16_t clientPort;  // network order (as in sockaddr)
        uint32_t clientAddr;  // network order
        uint32_t clientIp;    // host order
        int64_t t0Us;
        int64_t retryUs;
        int64_t failUs;
        uint16_t qLen;
        uint8_t* query;  // pendingQueryMax bytes in the pool
    };
    struct FlagMemo {
        uint32_t ip;
        int16_t rule;
        uint32_t atMs;
    };

    static constexpr size_t kPacketMax = 1500;
    static constexpr size_t kPendingQueryMax = 512;
    static constexpr size_t kFlagMemo = 32;

    // sockets
    void openSockets();
    void closeSockets();
    int openUdp(uint16_t port, bool loopback, bool reuse);
    void joinMdns();
    // packet paths
    void onQuery(const uint8_t* p, size_t n, uint32_t addrNet, uint16_t portNet);
    void onUpstream(const uint8_t* p, size_t n, uint32_t fromIp);
    void onMdns(const uint8_t* p, size_t n, uint32_t fromIp);
    void onDhcp(const uint8_t* p, size_t n);
    void sendTo(int sock, const uint8_t* p, size_t n, uint32_t addrNet, uint16_t portNet);
    bool sendUpstream(Pending& e, uint8_t idx);
    // timers
    void service(int64_t nowUs);
    void servicePending(int64_t nowUs);
    void serviceScan(int64_t nowUs);
    void pollArp(int64_t nowUs);
    void flushBatches(int64_t nowUs);
    // records + events
    void record(uint32_t ip, const DnsQuestion& q, uint8_t rcode, uint16_t latencyMs, uint8_t flags, uint8_t category);
    void maybeFlag(uint32_t ip, const DnsQuestion& q, const Watchlist::Match& m);
    bool emitFlag(uint32_t ip, const DnsQuestion& q, const Watchlist::Match& m);
    size_t buildBatch(uint8_t* buf, size_t cap, bool& more);
    bool clientAllowed(uint32_t ip) const;
    uint32_t nowMs() const;

    // tools (NetTools.cpp)
    static void toolSetWatchlist(mcp::ToolContext& ctx);
    static void toolGetWatchlist(mcp::ToolContext& ctx);
    static void toolNetworkStats(mcp::ToolContext& ctx);
    static void toolLocalDevices(mcp::ToolContext& ctx);
    static void toolQueryLog(mcp::ToolContext& ctx);
    static void toolScanLan(mcp::ToolContext& ctx);
    static void toolFlushCache(mcp::ToolContext& ctx);
    static void taskEntry(void* self);
    void persistWatchlist();

    friend struct NetObserveTestAccess;

    Config cfg_;
    Platform* platform_ = nullptr;
    mcp::Uplink* uplink_ = nullptr;
    mutable os::Mutex mu_;  // watchlist, cache, log, clients, counters, upstream stats, scan

    Watchlist* active_ = nullptr;   // pool-allocated
    Watchlist* staging_ = nullptr;  // set_watchlist builds here, then swaps
    uint8_t* blob_ = nullptr;       // Watchlist::kBlobMax
    DnsCache cache_;
    QueryLog log_;
    ClientTable clients_;
    Pending* pending_ = nullptr;
    uint8_t* pendingPool_ = nullptr;
    FlagMemo flags_[kFlagMemo] = {};
    uint8_t* pkt_ = nullptr;   // kPacketMax receive/reply buffer (netobs task only)
    uint8_t* pkt2_ = nullptr;  // kPacketMax second buffer (cache hit / SERVFAIL replies)

    int dnsSock_ = -1;
    int upSock_ = -1;
    int mdnsSock_ = -1;
    int dhcpSock_ = -1;
    std::atomic<uint16_t> listenPort_{0};
    std::atomic<uint16_t> pendingCount_{0};
    std::atomic<uint32_t> ipShared_{0};  // ip_ for other tasks (tools, HELLO)
    std::atomic<bool> reconfig_{false};
    bool wantForward_ = false;  // guarded by mu_ (set by reconfigure)
    uint32_t wantUpstreams_[2] = {};
    uint8_t wantUpstreamCount_ = 0;

    // station address tracking (netobs task)
    uint32_t ip_ = 0;
    uint32_t mask_ = 0;
    int64_t nextBindRetryUs_ = 0;
    int64_t nextArpUs_ = 0;
    int64_t nextResolversUs_ = 0;
    int64_t nextBatchUs_ = 0;
    int64_t lastBatchUs_ = 0;
    int64_t lastClientQueryUs_ = 0;

    // scan state (mu_)
    bool scanActive_ = false;
    uint32_t scanBase_ = 0;  // network address of the /24
    uint16_t scanNext_ = 1;
    int64_t scanNextUs_ = 0;
    int64_t scanEndUs_ = 0;

    // counters (mu_)
    tools::DnsCountersSnap c_;
    UpstreamStats up_[2];
    uint32_t batchSeq_ = 0;
    std::atomic<uint32_t> rid_{0x40000000u};
    std::atomic<uint32_t> runUsMax_{0};
    uint32_t flagsDropped_ = 0;
};

}  // namespace netobs
