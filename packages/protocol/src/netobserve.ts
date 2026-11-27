/*
 * AEDI - IONITY GLOBAL | @ionity/ewp - Edge Wire Protocol v1 | netobserve.ts
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * NetObserve (PLAN-v3): every node that the router's DHCP names as the LAN DNS
 * server forwards each query upstream and reports WHO asked for WHAT.
 *
 * One contract for both node types - the ESP-IDF firmware (C++) and the
 * Raspberry Pi agent (Python) encode exactly these bodies, so the host has one
 * decoder and one storage path:
 *
 *   EVENT kind DNS_BATCH   (topic ev, QoS 1)  every flush interval, possibly
 *                                             split over frames (`more`)
 *   EVENT kind DNS_FLAGGED (topic ev, QoS 1)  immediately, for ALERT rules
 *   TELEMETRY.dns          (topic tm, QoS 0)  counters with every heartbeat
 *   HELLO.net              (topic hello)      mode, upstreams, watchlist version
 *
 * Only metadata crosses the wire (client address, MAC, host name, the name
 * asked for, type, result, latency) - never a payload or an answer address.
 *
 * Time: nodes have no RTC, so every entry carries `ageMs` = how long before
 * the frame was published it happened. The host stamps receive-time - ageMs.
 */
import { defineKeys } from './keys.ts';
import type { DnsMode, PingError, WatchlistOp } from './ids.ts';
import { EWP_LIMITS } from './constants.ts';
import { utf8ByteLength } from './scalars.ts';
import type { Bytes, FixedBytes, U16, U32, U8, Utf8 } from './scalars.ts';

/* ------------------------------------------------------------------------- */
/* Shared tuples                                                              */
/* ------------------------------------------------------------------------- */

/**
 * A LAN client as the node knows it: [IPv4, MAC (6 B, or 0 B while ARP has not
 * resolved it), host name ("" when unknown)]. Queries reference clients by
 * index into the frame's `clients` array, so a chatty phone costs its address
 * once per frame, not once per query.
 */
export type DnsClient = readonly [ip: FixedBytes<4>, mac: Bytes<6>, hostname: Utf8<63>];

/**
 * One query (or a run of identical queries, see `count`):
 * [ageMs, client index, QNAME (lower case, no trailing dot), QTYPE, RCODE,
 *  latency ms (0 for cache hits), count, DnsQueryFlag bitset, category id (0 = none)]
 *
 * Identical (client, qname, qtype) within one batch are folded into one entry:
 * ageMs is the NEWEST occurrence, count how many there were.
 */
export type DnsQuery = readonly [
  ageMs: U32,
  client: U8,
  qname: Utf8<253>,
  qtype: U16,
  rcode: U8,
  latencyMs: U16,
  count: U16,
  flags: U8,
  category: U8,
];

/** [category id 1..32, name e.g. "video_streaming:youtube"] */
export type WatchCategory = readonly [id: U8, name: Utf8<48>];

/**
 * [suffix, category id, RuleFlag bitset]. Matching is per DNS label from the
 * right, case-insensitive: "youtube.com" matches youtube.com and
 * www.youtube.com but not notyoutube.com. With SUBDOMAINS_ONLY ("*.x") the
 * bare suffix itself does not match.
 */
export type WatchRule = readonly [suffix: Utf8<96>, category: U8, flags: U8];

/* ------------------------------------------------------------------------- */
/* EVENT bodies                                                               */
/* ------------------------------------------------------------------------- */

export interface DnsBatch {
  /** per-boot batch counter; a gap means a lost frame */
  readonly seq: U32;
  /** how much time this batch covers (since the previous one) */
  readonly windowMs: U32;
  readonly clients: readonly DnsClient[];
  readonly queries: readonly DnsQuery[];
  /** queries the node could not keep (ring full) since the previous batch */
  readonly dropped?: U32;
  /** another frame with the same seq follows (the window did not fit one frame) */
  readonly more?: boolean;
}
export const DnsBatchKey = defineKeys<DnsBatch>()({
  seq: 1,
  windowMs: 2,
  clients: 3,
  queries: 4,
  dropped: 5,
  more: 6,
});

export interface DnsFlag {
  readonly client: DnsClient;
  readonly qname: Utf8<253>;
  readonly qtype: U16;
  readonly category: U8;
  /** the watchlist suffix that matched */
  readonly rule: Utf8<96>;
  readonly ageMs: U32;
  /** watchlist version the match was made against */
  readonly watchVer: U32;
}
export const DnsFlagKey = defineKeys<DnsFlag>()({
  client: 1,
  qname: 2,
  qtype: 3,
  category: 4,
  rule: 5,
  ageMs: 6,
  watchVer: 7,
});

/* ------------------------------------------------------------------------- */
/* TELEMETRY / HELLO additions                                                */
/* ------------------------------------------------------------------------- */

/** Monotonic since boot. The host derives rates from consecutive samples. */
export interface DnsCounters {
  readonly mode: DnsMode;
  readonly queries: U32;
  readonly forwarded: U32;
  readonly cacheHits: U32;
  readonly upstreamFail: U32;
  /** queries dropped: rate limit, pending table full, malformed */
  readonly dropped: U32;
  readonly flagged: U32;
  /** distinct clients seen in the last 10 minutes */
  readonly clients: U16;
  /** entries in the local-device table */
  readonly lanDevices: U16;
}
export const DnsCountersKey = defineKeys<DnsCounters>()({
  mode: 1,
  queries: 2,
  forwarded: 3,
  cacheHits: 4,
  upstreamFail: 5,
  dropped: 6,
  flagged: 7,
  clients: 8,
  lanDevices: 9,
});

export interface NetObserveInfo {
  readonly mode: DnsMode;
  /** UDP (and, with NetCap.DNS_TCP, TCP) port the forwarder listens on - 53 in the field */
  readonly port: U16;
  readonly upstreams: readonly FixedBytes<4>[];
  /** NetCap bitset */
  readonly caps: U8;
  readonly watchVer: U32;
  readonly watchRules: U16;
}
export const NetObserveInfoKey = defineKeys<NetObserveInfo>()({
  mode: 1,
  port: 2,
  upstreams: 3,
  caps: 4,
  watchVer: 5,
  watchRules: 6,
});

/* ------------------------------------------------------------------------- */
/* Tool bodies (0x40 - 0x47, 0xF0)                                            */
/* ------------------------------------------------------------------------- */

/** 0x40 set_watchlist. REPLACE clears the list first; APPEND must carry the same version. */
export interface SetWatchlistArgs {
  readonly op: WatchlistOp;
  readonly version: U32;
  readonly rules: readonly WatchRule[];
  readonly categories?: readonly WatchCategory[];
}
export const SetWatchlistArgsKey = defineKeys<SetWatchlistArgs>()({ op: 1, version: 2, rules: 3, categories: 4 });

export interface WatchlistResult {
  readonly version: U32;
  readonly rules: U16;
  readonly categories: U8;
}
export const WatchlistResultKey = defineKeys<WatchlistResult>()({ version: 1, rules: 2, categories: 3 });

/** 0x41 get_watchlist result - the whole list as the node holds it. */
export interface GetWatchlistResult {
  readonly version: U32;
  readonly rules: readonly WatchRule[];
  readonly categories: readonly WatchCategory[];
}
export const GetWatchlistResultKey = defineKeys<GetWatchlistResult>()({ version: 1, rules: 2, categories: 3 });

/** [upstream IPv4, answers, failures, mean latency ms] */
export type UpstreamStat = readonly [ip: FixedBytes<4>, ok: U32, fail: U32, meanMs: U16];

/** 0x42 get_network_stats result. */
export interface NetworkStatsResult {
  readonly dns: DnsCounters;
  readonly upstreams: readonly UpstreamStat[];
  readonly cacheEntries: U16;
  /** queries waiting for an upstream answer right now */
  readonly pending: U16;
  /** ms since the forwarder last answered someone other than the node itself */
  readonly lastClientQueryAgoMs?: U32;
}
export const NetworkStatsResultKey = defineKeys<NetworkStatsResult>()({
  dns: 1,
  upstreams: 2,
  cacheEntries: 3,
  pending: 4,
  lastClientQueryAgoMs: 5,
});

/** [IPv4, MAC (0 or 6 B), host name, seconds since last seen, queries, DiscoverySource bitset] */
export type LocalDevice = readonly [ip: FixedBytes<4>, mac: Bytes<6>, hostname: Utf8<63>, ageS: U32, queries: U32, sources: U8];

export interface GetLocalDevicesArgs {
  /** default and maximum EWP_LIMITS.localDevices; the device trims to its uplink frame */
  readonly limit?: U8;
}
export const GetLocalDevicesArgsKey = defineKeys<GetLocalDevicesArgs>()({ limit: 1 });

export interface LocalDevicesResult {
  readonly devices: readonly LocalDevice[];
  /** entries the node holds in total (may exceed devices.length) */
  readonly total: U16;
}
export const LocalDevicesResultKey = defineKeys<LocalDevicesResult>()({ devices: 1, total: 2 });

export interface QueryDnsLogArgs {
  /** newest first, at most EWP_LIMITS.dnsLogQuery (default 16) */
  readonly limit?: U8;
  /** only this client */
  readonly client?: FixedBytes<4>;
  readonly flaggedOnly?: boolean;
}
export const QueryDnsLogArgsKey = defineKeys<QueryDnsLogArgs>()({ limit: 1, client: 2, flaggedOnly: 3 });

/** 0x44 query_dns_log result - same tuples as DNS_BATCH, newest first. */
export interface DnsLogResult {
  readonly clients: readonly DnsClient[];
  readonly queries: readonly DnsQuery[];
}
export const DnsLogResultKey = defineKeys<DnsLogResult>()({ clients: 1, queries: 2 });

/** 0x45 scan_lan result - the sweep runs in the background. */
export interface ScanLanResult {
  readonly targets: U16;
  readonly etaMs: U32;
}
export const ScanLanResultKey = defineKeys<ScanLanResult>()({ targets: 1, etaMs: 2 });

/** 0x46 flush_dns_cache result. */
export interface FlushDnsCacheResult {
  readonly flushed: U16;
}
export const FlushDnsCacheResultKey = defineKeys<FlushDnsCacheResult>()({ flushed: 1 });

/** 0x47 ping_host. Bounds are EWP_LIMITS.ping*; out-of-range args are INVALID_ARGS on the node. */
export interface PingHostArgs {
  /** host name or dotted IPv4 */
  readonly target: Utf8<253>;
  /** 1..pingCountMax, default 4 */
  readonly count?: U8;
  /** per echo, pingTimeoutMinMs..pingTimeoutMaxMs, default 1000 */
  readonly timeoutMs?: U16;
  /** gap between echoes, pingIntervalMinMs..pingIntervalMaxMs, default 1000 */
  readonly intervalMs?: U16;
}
export const PingHostArgsKey = defineKeys<PingHostArgs>()({ target: 1, count: 2, timeoutMs: 3, intervalMs: 4 });

/** RTT fields are present only when received > 0. */
export interface PingHostResult {
  readonly error: PingError;
  /** the address actually pinged (absent when the name did not resolve) */
  readonly resolvedIp?: FixedBytes<4>;
  readonly sent: U8;
  readonly received: U8;
  readonly rttMinMs?: U16;
  readonly rttAvgMs?: U16;
  readonly rttMaxMs?: U16;
}
export const PingHostResultKey = defineKeys<PingHostResult>()({
  error: 1,
  resolvedIp: 2,
  sent: 3,
  received: 4,
  rttMinMs: 5,
  rttAvgMs: 6,
  rttMaxMs: 7,
});

export const PING_DEFAULTS = { count: 4, timeoutMs: 1000, intervalMs: 1000 } as const;

/** RFC 1123 host name (letters, digits, hyphens; dot-separated labels) or dotted IPv4. */
const PING_TARGET_RE = /^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$/;

/**
 * Checks ping_host arguments against the contract and fills defaults. The
 * gateway calls it before encoding; nodes apply the same bounds on decode.
 * Throws RangeError naming the offending field.
 */
export function checkPingHostArgs(a: {
  target: string;
  count?: number;
  timeoutMs?: number;
  intervalMs?: number;
}): Required<{ target: string; count: number; timeoutMs: number; intervalMs: number }> {
  const target = a.target.trim();
  if (utf8ByteLength(target) > EWP_LIMITS.qnameBytes || !PING_TARGET_RE.test(target)) {
    throw new RangeError(`ping_host.target: not a host name or IPv4 address: ${JSON.stringify(a.target)}`);
  }
  const L = EWP_LIMITS;
  const range = (name: string, v: number | undefined, def: number, lo: number, hi: number): number => {
    const n = v ?? def;
    if (!Number.isInteger(n) || n < lo || n > hi) throw new RangeError(`ping_host.${name}: ${n} not in ${lo}..${hi}`);
    return n;
  };
  return {
    target,
    count: range('count', a.count, PING_DEFAULTS.count, 1, L.pingCountMax),
    timeoutMs: range('timeoutMs', a.timeoutMs, PING_DEFAULTS.timeoutMs, L.pingTimeoutMinMs, L.pingTimeoutMaxMs),
    intervalMs: range('intervalMs', a.intervalMs, PING_DEFAULTS.intervalMs, L.pingIntervalMinMs, L.pingIntervalMaxMs),
  };
}

/** 0xF0 reboot. */
export interface RebootArgs {
  /** default 500 ms, at most 10 000 - the RES is published before the restart */
  readonly delayMs?: U16;
}
export const RebootArgsKey = defineKeys<RebootArgs>()({ delayMs: 1 });

export interface RebootResult {
  readonly inMs: U16;
}
export const RebootResultKey = defineKeys<RebootResult>()({ inMs: 1 });
