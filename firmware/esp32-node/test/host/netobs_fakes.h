/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/netobs_fakes.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * NetObserve test doubles: a Platform with an in-memory ARP cache and NVS
 * blob, upstream resolvers on loopback (127.0.0.1 / 127.0.0.2) that answer,
 * stay silent or misbehave on demand, and DNS message builders.
 */
#pragma once

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "netobserve/NetObserve.h"

namespace nf {

using Bytes = std::vector<uint8_t>;

class Platform final : public netobs::Platform {
public:
    std::mutex mu;
    uint32_t ip = 0xC0A80032;  // 192.168.0.50: the node itself
    uint32_t mask = 0xFFFFFF00;
    std::map<uint32_t, std::vector<uint8_t>> arp;
    std::vector<uint32_t> requested;
    std::vector<uint32_t> resolvers;
    std::atomic<bool> link{true};
    std::atomic<int> alerts{0};
    std::atomic<uint8_t> lastAlertCategory{0};
    Bytes blob;
    int saves = 0;

    bool stationIp(uint32_t& a, uint32_t& m) override {
        std::lock_guard<std::mutex> lk(mu);
        a = ip;
        m = mask;
        return ip != 0;
    }
    bool arpLookup(uint32_t a, uint8_t mac[6]) override {
        std::lock_guard<std::mutex> lk(mu);
        auto it = arp.find(a);
        if (it == arp.end()) return false;
        memcpy(mac, it->second.data(), 6);
        return true;
    }
    size_t arpTable(netobs::ArpEntry* out, size_t max) override {
        std::lock_guard<std::mutex> lk(mu);
        size_t n = 0;
        for (auto& kv : arp) {
            if (n == max) break;
            out[n].ip = kv.first;
            memcpy(out[n].mac, kv.second.data(), 6);
            ++n;
        }
        return n;
    }
    size_t arpRequest(const uint32_t* ips, size_t n) override {
        std::lock_guard<std::mutex> lk(mu);
        for (size_t i = 0; i < n; ++i) {
            requested.push_back(ips[i]);
            // ".7" and ".42" exist on this fake LAN and answer ARP
            if ((ips[i] & 0xFF) == 7 || (ips[i] & 0xFF) == 42) {
                arp[ips[i]] = {0x3c, 0x22, 0xfb, 0x00, 0x00, static_cast<uint8_t>(ips[i] & 0xFF)};
            }
        }
        return n;
    }
    void useResolvers(const uint32_t* ips, size_t n) override {
        std::lock_guard<std::mutex> lk(mu);
        resolvers.assign(ips, ips + n);
    }
    bool linkUp() override { return link.load(); }
    void alert(uint8_t category, uint32_t) override {
        alerts.fetch_add(1);
        lastAlertCategory.store(category);
    }
    bool loadWatchlist(uint8_t* buf, size_t cap, size_t& len) override {
        std::lock_guard<std::mutex> lk(mu);
        if (blob.empty() || blob.size() > cap) return false;
        memcpy(buf, blob.data(), blob.size());
        len = blob.size();
        return true;
    }
    bool saveWatchlist(const uint8_t* buf, size_t len) override {
        std::lock_guard<std::mutex> lk(mu);
        blob.assign(buf, buf + len);
        ++saves;
        return true;
    }
};

/* ---- DNS messages --------------------------------------------------------------- */

inline void put16(Bytes& b, uint16_t v) {
    b.push_back(static_cast<uint8_t>(v >> 8));
    b.push_back(static_cast<uint8_t>(v));
}

inline void putName(Bytes& b, const std::string& name) {
    size_t start = 0;
    while (start < name.size()) {
        size_t dot = name.find('.', start);
        if (dot == std::string::npos) dot = name.size();
        b.push_back(static_cast<uint8_t>(dot - start));
        b.insert(b.end(), name.begin() + static_cast<long>(start), name.begin() + static_cast<long>(dot));
        start = dot + 1;
    }
    b.push_back(0);
}

inline Bytes query(uint16_t id, const std::string& name, uint16_t qtype = 1, bool edns = false) {
    Bytes b;
    put16(b, id);
    put16(b, 0x0100);  // RD
    put16(b, 1);
    put16(b, 0);
    put16(b, 0);
    put16(b, edns ? 1 : 0);
    putName(b, name);
    put16(b, qtype);
    put16(b, 1);
    if (edns) {  // OPT: root name, type 41, udp size 1232, ttl 0, rdlen 0
        b.push_back(0);
        put16(b, 41);
        put16(b, 1232);
        put16(b, 0);
        put16(b, 0);
        put16(b, 0);
    }
    return b;
}

/** An answer to `q` with one A record (name compressed to the question), TTL `ttl`. */
inline Bytes answer(const uint8_t* q, size_t qlen, uint32_t ttl, uint8_t rcode = 0) {
    // question end: header + name + 4
    size_t off = 12;
    while (off < qlen && q[off] != 0) off += 1u + q[off];
    const size_t qend = off + 1 + 4;
    // header built field by field (indexing a fresh vector trips GCC's -Wnull-dereference under TSan)
    Bytes b;
    b.reserve(qend + 16);
    b.push_back(q[0]);
    b.push_back(q[1]);
    b.push_back(static_cast<uint8_t>(0x80 | (q[2] & 0x01)));
    b.push_back(static_cast<uint8_t>(0x80 | rcode));
    put16(b, 1);
    put16(b, rcode == 0 ? 1 : 0);
    put16(b, 0);
    put16(b, 0);
    b.insert(b.end(), q + 12, q + qend);
    if (rcode == 0) {
        b.push_back(0xC0);
        b.push_back(0x0C);
        put16(b, 1);
        put16(b, 1);
        put16(b, static_cast<uint16_t>(ttl >> 16));
        put16(b, static_cast<uint16_t>(ttl));
        put16(b, 4);
        b.insert(b.end(), {93, 184, 216, 34});
    }
    return b;
}

/* ---- loopback upstream ------------------------------------------------------------ */

class Upstream {
public:
    enum Mode { Answer, Silent, WrongQuestion };
    std::atomic<int> mode{Answer};
    std::atomic<int> seen{0};
    std::atomic<uint32_t> ttl{300};

    bool start(const char* addr, uint16_t port) {
        fd_ = socket(AF_INET, SOCK_DGRAM, 0);
        const int one = 1;
        setsockopt(fd_, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
        sockaddr_in sa{};
        sa.sin_family = AF_INET;
        sa.sin_port = htons(port);
        inet_pton(AF_INET, addr, &sa.sin_addr);
        if (bind(fd_, reinterpret_cast<sockaddr*>(&sa), sizeof sa) != 0) return false;
        timeval tv{0, 50000};
        setsockopt(fd_, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
        t_ = std::thread([this] { loop(); });
        return true;
    }
    ~Upstream() {
        stop_.store(true);
        if (t_.joinable()) t_.join();
        if (fd_ >= 0) close(fd_);
    }

private:
    void loop() {
        uint8_t buf[1500];
        while (!stop_.load()) {
            sockaddr_in from{};
            socklen_t fl = sizeof from;
            const ssize_t n = recvfrom(fd_, buf, sizeof buf, 0, reinterpret_cast<sockaddr*>(&from), &fl);
            if (n <= 0) continue;
            seen.fetch_add(1);
            if (mode.load() == Silent) continue;
            Bytes r = answer(buf, static_cast<size_t>(n), ttl.load());
            if (mode.load() == WrongQuestion) r[13] ^= 0x01;  // first label byte differs
            sendto(fd_, r.data(), r.size(), 0, reinterpret_cast<sockaddr*>(&from), fl);
        }
    }
    int fd_ = -1;
    std::thread t_;
    std::atomic<bool> stop_{false};
};

/** A LAN client on loopback. */
class Client {
public:
    Client() {
        fd_ = socket(AF_INET, SOCK_DGRAM, 0);
        const int fl = fcntl(fd_, F_GETFL, 0);
        fcntl(fd_, F_SETFL, fl | O_NONBLOCK);
    }
    ~Client() { close(fd_); }
    void send(const Bytes& b, uint16_t port) {
        sockaddr_in sa{};
        sa.sin_family = AF_INET;
        sa.sin_port = htons(port);
        sa.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        sendto(fd_, b.data(), b.size(), 0, reinterpret_cast<sockaddr*>(&sa), sizeof sa);
    }
    bool recv(Bytes& out) {
        uint8_t buf[1500];
        const ssize_t n = ::recv(fd_, buf, sizeof buf, 0);
        if (n <= 0) return false;
        out.assign(buf, buf + n);
        return true;
    }

private:
    int fd_ = -1;
};

/** Send a query, drive the forwarder until the reply comes (or timeoutMs). */
inline bool exchange(netobs::NetObserve& nob, Client& c, const Bytes& q, Bytes& out, uint32_t timeoutMs = 2000) {
    if (nob.listenPort() == 0) nob.runOnce(1);  // the loop opens the sockets
    c.send(q, nob.listenPort());
    const int64_t until = os::nowUs() + static_cast<int64_t>(timeoutMs) * 1000;
    while (os::nowUs() < until) {
        nob.runOnce(10);
        if (c.recv(out)) return true;
    }
    return false;
}

inline void pump(netobs::NetObserve& nob, uint32_t ms) {
    const int64_t until = os::nowUs() + static_cast<int64_t>(ms) * 1000;
    while (os::nowUs() < until) nob.runOnce(10);
}

}  // namespace nf
