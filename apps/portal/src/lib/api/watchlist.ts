/*
 * AEDI - IONITY GLOBAL | @ionity/portal | api/watchlist.ts | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
 */

/** Same normalisation the host applies (app/rules/engine.py normalize_pattern), for early feedback. */
export function checkPattern(input: string): { ok: true; value: string } | { ok: false; error: string } {
  const p = input.trim().toLowerCase().replace(/\.$/, '');
  if (/^https?:\/\//.test(p) || p.includes('/')) return { ok: false, error: 'give a domain (youtube.com), not a URL' };
  const sub = p.startsWith('*.');
  const bare = sub ? p.slice(2) : p;
  if (bare.includes('*')) return { ok: false, error: "only a leading '*.' wildcard is supported" };
  const label = '[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?';
  if (!bare || new TextEncoder().encode(bare).length > 96 || !new RegExp(`^${label}(?:\\.${label})*$`).test(bare)) {
    return { ok: false, error: 'letters, digits, hyphens and dots only, at most 96 characters' };
  }
  if (!bare.includes('.')) return { ok: false, error: 'use at least two labels, e.g. youtube.com' };
  return { ok: true, value: sub ? `*.${bare}` : bare };
}

export function checkCategory(input: string): { ok: true; value: string } | { ok: false; error: string } {
  const c = input.trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9_-]{0,23}(?::[a-z0-9][a-z0-9_.-]{0,22})?$/.test(c)) {
    return { ok: false, error: 'lower case letters, digits, _ and -; optionally group:name, e.g. social:instagram' };
  }
  return { ok: true, value: c };
}
