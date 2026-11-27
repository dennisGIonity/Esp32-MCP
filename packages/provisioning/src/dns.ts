/*
 * AEDI - IONITY GLOBAL | @ionity/prov - ionity-prov/2 | dns.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The LAN DNS forwarder settings (NetObserve, firmware 3.1+):
 *
 *   dns_mode      "off" | "forward"   forward = answer UDP 53 for the LAN and
 *                                     report who asked for what (router DHCP DNS
 *                                     must point at the board's reserved IP)
 *   dns_upstream  "" | "a.b.c.d[,e.f.g.h]"   resolvers the board forwards to;
 *                                     "" = the firmware default 1.1.1.1,8.8.8.8
 *
 * Same rules as the firmware (components/provisioning Validate.cpp): dotted
 * quads, no leading zeros, at most two, no duplicates, and never a loopback,
 * "this network" (0/8), multicast, reserved or broadcast address - a board
 * forwarding to itself or to nothing would take the whole LAN's DNS down.
 */

export const DNS_MODES = ['off', 'forward'] as const;
export type DnsMode = (typeof DNS_MODES)[number];

/** EWP_LIMITS.dnsUpstreams */
export const DNS_UPSTREAMS_MAX = 2;
/** What an empty dns_upstream means on the board. */
export const DNS_UPSTREAM_DEFAULT = '1.1.1.1,8.8.8.8';

const OCTET = /^(0|[1-9][0-9]{0,2})$/;

/** Parse one IPv4 dotted quad strictly; null when it is not one. */
export function parseIpv4(s: string): [number, number, number, number] | null {
  const parts = s.split('.');
  if (parts.length !== 4) return null;
  const out: number[] = [];
  for (const p of parts) {
    if (!OCTET.test(p)) return null;
    const n = Number(p);
    if (n > 255) return null;
    out.push(n);
  }
  return out as [number, number, number, number];
}

/** A resolver address a board may forward to. */
export function usableUpstream(ip: readonly number[]): boolean {
  const a = ip[0] ?? 0;
  return !(a === 0 || a === 127 || a >= 224);
}

export type UpstreamResult = { readonly ok: true; readonly value: string } | { readonly ok: false; readonly error: string };

/** "" stays "" (firmware default); otherwise canonical "a.b.c.d" or "a.b.c.d,e.f.g.h". */
export function parseDnsUpstream(input: string): UpstreamResult {
  const s = input.trim();
  if (s === '') return { ok: true, value: '' };
  const items = s.split(',').map((x) => x.trim());
  if (items.length > DNS_UPSTREAMS_MAX) return { ok: false, error: `at most ${DNS_UPSTREAMS_MAX} upstream resolvers` };
  const seen = new Set<string>();
  for (const it of items) {
    const ip = parseIpv4(it);
    if (!ip) return { ok: false, error: `"${it}" is not an IPv4 address (e.g. 1.1.1.1)` };
    if (!usableUpstream(ip)) return { ok: false, error: `${it} cannot be a DNS upstream (loopback, multicast or reserved)` };
    if (seen.has(it)) return { ok: false, error: `${it} is listed twice` };
    seen.add(it);
  }
  return { ok: true, value: items.join(',') };
}
