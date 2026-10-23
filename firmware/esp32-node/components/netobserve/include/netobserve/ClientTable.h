/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/ClientTable.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The node's table of LAN devices (LocalDevice in netobserve.ts): IPv4, MAC,
 * host name, when last seen, how many DNS queries, and which sources saw it
 * (ARP / DHCP / mDNS / DNS - EWP_DISC_*). EWP_LIMIT_LOCAL_DEVICES entries;
 * when full, the device not seen for longest is replaced.
 *
 * Keyed by IPv4 address; a DHCP request (MAC + host name, the address it is
 * about to get) re-keys an entry when the MAC is already known under another
 * address, so a phone that moved to a new lease keeps its name.
 * THREADS: none here - NetObserve's mutex.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "ewp_protocol.h"

namespace netobs {

struct LanDevice {
    uint32_t ip;  // host order, 0 = slot unused
    uint8_t mac[6];
    bool hasMac;
    char hostname[EWP_LIMIT_HOSTNAME_BYTES + 1];
    uint32_t firstSeenMs;
    uint32_t lastSeenMs;
    uint32_t lastQueryMs;  // 0 = never asked us anything
    uint32_t queries;
    uint8_t sources;  // EWP_DISC_* bitset
};

class ClientTable {
public:
    static constexpr size_t kMax = EWP_LIMIT_LOCAL_DEVICES;

    /** Seen at this address (creates the entry). Returns it, never nullptr. */
    LanDevice& touch(uint32_t ip, uint32_t nowMs, uint8_t source);
    void setMac(uint32_t ip, const uint8_t mac[6], uint32_t nowMs, uint8_t source);
    /** Host name for an address (mDNS A record, DHCP). Sanitised to a DNS-ish label set. */
    void setHostname(uint32_t ip, const char* name, size_t n, uint32_t nowMs, uint8_t source);
    /** DHCP: MAC + name, with the address when the request carries one (0 = unknown yet). */
    void dhcp(const uint8_t mac[6], uint32_t ip, const char* name, size_t n, uint32_t nowMs);

    const LanDevice* find(uint32_t ip) const;
    LanDevice* find(uint32_t ip);
    size_t size() const;
    /** Distinct devices that sent a DNS query within the last windowMs. */
    uint16_t activeClients(uint32_t nowMs, uint32_t windowMs) const;
    /** i-th device by most recently seen (0 = newest). nullptr past the end. */
    const LanDevice* byRecency(size_t i, uint32_t nowMs) const;

private:
    LanDevice* slotFor(uint32_t ip, uint32_t nowMs);
    LanDevice entries_[kMax] = {};
};

}  // namespace netobs
