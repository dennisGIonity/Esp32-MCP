/*
 * AEDI - IONITY GLOBAL | @ionity/ewp - Edge Wire Protocol v1 | watchlist.ts
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The built-in watchlist - ONE list for every party:
 *   - the firmware boots with it (generated into ewp_protocol.h) so a node
 *     flags YouTube before the host has ever pushed a list,
 *   - the Pi agent and the fleet host seed it from ewp_protocol.py,
 *   - the host's rule engine marks these rules `builtin` (they can be
 *     disabled, not deleted).
 *
 * Pattern syntax (host side, API/MCP): "example.com" matches the domain and
 * every subdomain; "*.example.com" matches subdomains only. The device form is
 * [suffix, category, RuleFlag] - see WatchRule in netobserve.ts.
 */
import { RuleFlag } from './ids.ts';

export interface DefaultCategory {
  readonly id: number;
  /** "<group>:<service>" - the group drives dashboards, the service names the alert */
  readonly name: string;
  readonly label: string;
}

export interface DefaultRule {
  /** host-side pattern ("*.x" = subdomains only) */
  readonly pattern: string;
  readonly category: number;
  /** RuleFlag bitset (SUBDOMAINS_ONLY is derived from the pattern) */
  readonly flags: number;
}

export const DEFAULT_CATEGORIES: readonly DefaultCategory[] = [
  { id: 1, name: 'video_streaming:youtube', label: 'YouTube' },
  { id: 2, name: 'video_streaming:tiktok', label: 'TikTok' },
];

const A = RuleFlag.ALERT;

export const DEFAULT_RULES: readonly DefaultRule[] = [
  // YouTube: web, app API, video CDN, thumbnails, short links, embeds, kids
  { pattern: 'youtube.com', category: 1, flags: A },
  { pattern: 'youtu.be', category: 1, flags: A },
  { pattern: '*.googlevideo.com', category: 1, flags: A },
  { pattern: '*.ytimg.com', category: 1, flags: A },
  { pattern: 'youtubei.googleapis.com', category: 1, flags: A },
  { pattern: 'youtube.googleapis.com', category: 1, flags: A },
  { pattern: 'youtube-nocookie.com', category: 1, flags: A },
  { pattern: 'youtubekids.com', category: 1, flags: A },
  { pattern: 'yt3.ggpht.com', category: 1, flags: A },
  // TikTok
  { pattern: 'tiktok.com', category: 2, flags: A },
  { pattern: 'tiktokv.com', category: 2, flags: A },
  { pattern: 'tiktokcdn.com', category: 2, flags: A },
  { pattern: 'tiktokcdn-us.com', category: 2, flags: A },
];

/** Version of the built-in list on a node that has never been sent one. */
export const DEFAULT_WATCHLIST_VERSION = 1;

/** "*.x" -> ["x", SUBDOMAINS_ONLY]; "x" -> ["x", 0]. Lower-cased, no trailing dot. */
export function patternToSuffix(pattern: string): { suffix: string; flags: number } {
  const p = pattern.trim().toLowerCase().replace(/\.$/, '');
  if (p.startsWith('*.')) return { suffix: p.slice(2), flags: RuleFlag.SUBDOMAINS_ONLY };
  return { suffix: p, flags: 0 };
}
