/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/Discovery.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Passive LAN discovery - parsers only (the sockets live in NetObserve):
 *
 *   mDNS (224.0.0.251:5353)  every A record a device announces about itself
 *                            ("kitchen-tv.local -> 192.168.0.42") names an address.
 *   DHCP (broadcast to :67)  a client's DISCOVER/REQUEST carries its MAC
 *                            (chaddr), usually its host name (option 12) and the
 *                            address it wants (option 50 / ciaddr).
 *
 * Both only READ what devices broadcast to everyone on the segment anyway.
 * Bounds-checked against the datagram; hostile input is the normal case.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace netobs {

/** One host-name/address pair from an mDNS message. */
using MdnsHostFn = void (*)(void* user, uint32_t ip, const char* name, size_t nameLen);

/**
 * Walk every record of an mDNS message and report A records whose name ends
 * in ".local" (name without ".local", ip host order). Returns how many were
 * reported; malformed messages report what was valid before the damage.
 */
int parseMdns(const uint8_t* p, size_t n, MdnsHostFn fn, void* user);

struct DhcpInfo {
    uint8_t mac[6];
    uint8_t msgType;  // option 53 (1 DISCOVER, 3 REQUEST, 8 INFORM ...), 0 = absent
    uint32_t ip;      // ciaddr, else option 50, else 0 (host order)
    char hostname[64];
    uint8_t hostnameLen;
};

/** A BOOTREQUEST over Ethernet/Wi-Fi (htype 1, hlen 6) with the DHCP magic cookie. */
bool parseDhcpRequest(const uint8_t* p, size_t n, DhcpInfo& out);

}  // namespace netobs
