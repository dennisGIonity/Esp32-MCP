/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/ClientTable.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "netobserve/ClientTable.h"

#include <string.h>

namespace netobs {
namespace {

/** Host names go into CBOR text and dashboards: keep [A-Za-z0-9-_.], replace the rest with '-'. */
size_t cleanName(const char* in, size_t n, char* out, size_t cap) {
    size_t o = 0;
    for (size_t i = 0; i < n && o + 1 < cap; ++i) {
        const char c = in[i];
        const bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' ||
                        c == '_' || c == '.';
        out[o++] = ok ? c : '-';
    }
    while (o > 0 && (out[o - 1] == '.' || out[o - 1] == '-')) --o;
    out[o] = '\0';
    return o;
}

}  // namespace

LanDevice* ClientTable::find(uint32_t ip) {
    if (ip == 0) return nullptr;
    for (LanDevice& d : entries_) {
        if (d.ip == ip) return &d;
    }
    return nullptr;
}

const LanDevice* ClientTable::find(uint32_t ip) const { return const_cast<ClientTable*>(this)->find(ip); }

LanDevice* ClientTable::slotFor(uint32_t ip, uint32_t nowMs) {
    LanDevice* d = find(ip);
    if (d != nullptr) return d;
    LanDevice* victim = &entries_[0];
    for (LanDevice& e : entries_) {
        if (e.ip == 0) {
            victim = &e;
            break;
        }
        if (nowMs - e.lastSeenMs > nowMs - victim->lastSeenMs) victim = &e;  // longest unseen (wrap-safe)
    }
    *victim = LanDevice{};
    victim->ip = ip;
    victim->firstSeenMs = nowMs;
    victim->lastSeenMs = nowMs;
    return victim;
}

LanDevice& ClientTable::touch(uint32_t ip, uint32_t nowMs, uint8_t source) {
    LanDevice* d = slotFor(ip, nowMs);
    d->lastSeenMs = nowMs;
    d->sources |= source;
    if (source & EWP_DISC_DNS) {
        d->lastQueryMs = nowMs == 0 ? 1 : nowMs;
        if (d->queries != 0xFFFFFFFFu) ++d->queries;
    }
    return *d;
}

void ClientTable::setMac(uint32_t ip, const uint8_t mac[6], uint32_t nowMs, uint8_t source) {
    if (ip == 0) return;
    LanDevice& d = touch(ip, nowMs, source);
    memcpy(d.mac, mac, 6);
    d.hasMac = true;
}

void ClientTable::setHostname(uint32_t ip, const char* name, size_t n, uint32_t nowMs, uint8_t source) {
    if (ip == 0 || name == nullptr || n == 0) return;
    LanDevice& d = touch(ip, nowMs, source);
    char tmp[EWP_LIMIT_HOSTNAME_BYTES + 1];
    if (cleanName(name, n, tmp, sizeof tmp) > 0) memcpy(d.hostname, tmp, sizeof tmp);
}

void ClientTable::dhcp(const uint8_t mac[6], uint32_t ip, const char* name, size_t n, uint32_t nowMs) {
    // Known by MAC under an old address? Carry the entry over to the new one.
    LanDevice* known = nullptr;
    for (LanDevice& e : entries_) {
        if (e.ip != 0 && e.hasMac && memcmp(e.mac, mac, 6) == 0) known = &e;
    }
    if (ip == 0) {
        if (known == nullptr) return;  // nothing to attach the name to yet (DISCOVER with no address)
        ip = known->ip;
    } else if (known != nullptr && known->ip != ip) {
        LanDevice* clash = find(ip);
        if (clash != nullptr) clash->ip = 0;  // the address now belongs to this MAC
        known->ip = ip;
    }
    setMac(ip, mac, nowMs, EWP_DISC_DHCP);
    if (n > 0) setHostname(ip, name, n, nowMs, EWP_DISC_DHCP);
}

size_t ClientTable::size() const {
    size_t n = 0;
    for (const LanDevice& d : entries_) n += d.ip != 0 ? 1 : 0;
    return n;
}

uint16_t ClientTable::activeClients(uint32_t nowMs, uint32_t windowMs) const {
    uint16_t n = 0;
    for (const LanDevice& d : entries_) {
        if (d.ip != 0 && d.lastQueryMs != 0 && nowMs - d.lastQueryMs <= windowMs) ++n;
    }
    return n;
}

const LanDevice* ClientTable::byRecency(size_t i, uint32_t nowMs) const {
    // O(n^2) over <= 128 entries, only for get_local_devices (an occasional tool call).
    // Ages (now - lastSeen) are wrap-safe where raw uptime stamps are not (49.7 days).
    const LanDevice* prev = nullptr;
    for (size_t k = 0; k <= i; ++k) {
        const LanDevice* best = nullptr;
        for (const LanDevice& d : entries_) {
            if (d.ip == 0) continue;
            const uint32_t age = nowMs - d.lastSeenMs;
            if (prev != nullptr) {
                const uint32_t prevAge = nowMs - prev->lastSeenMs;
                if (age < prevAge || (age == prevAge && &d <= prev)) continue;
            }
            if (best == nullptr) {
                best = &d;
                continue;
            }
            const uint32_t bestAge = nowMs - best->lastSeenMs;
            if (age < bestAge || (age == bestAge && &d < best)) best = &d;
        }
        if (best == nullptr) return nullptr;
        prev = best;
    }
    return prev;
}

}  // namespace netobs
