/*
 * AEDI - IONITY GLOBAL | @ionity/portal | FlaggedBanner.tsx | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
 *
 * Flagged Activity Banner: which device is hitting a watched category right
 * now ("Kitchen TV - YouTube - 41 hits"). Shown on every tab, announced once
 * per new episode (not per hit).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { activeFlags } from '../../lib/api/live.ts';
import { useAnnounce } from '../../ui/announcer.tsx';
import { useHost } from '../fleet/HostContext.tsx';

export function categoryText(category: string): string {
  // "video_streaming:youtube" -> "YouTube (video streaming)"
  const [group, name] = category.includes(':') ? category.split(':', 2) : ['', category];
  const nice = (s: string) => s.replace(/_/g, ' ');
  const known: Record<string, string> = { youtube: 'YouTube', tiktok: 'TikTok', netflix: 'Netflix' };
  const label = known[name ?? ''] ?? nice(name ?? category);
  return group ? `${label} (${nice(group)})` : label;
}

export function FlaggedBanner({ onShow }: { readonly onShow?: () => void }) {
  const host = useHost();
  const announce = useAnnounce();
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 15_000);
    return () => clearInterval(t);
  }, []);
  const active = useMemo(() => activeFlags(host.live.flags, Math.max(now, Date.now() / 1000)), [host.live.flags, now]);

  const seen = useRef(new Set<number>());
  useEffect(() => {
    for (const f of active) {
      if (seen.current.has(f.id)) continue;
      seen.current.add(f.id);
      announce(`Flagged: ${f.label || f.hostname || f.client_ip} is using ${categoryText(f.category)}`, { assertive: true });
    }
  }, [active, announce]);

  if (!active.length) return null;
  return (
    <section className="border-b-4 border-orange-brand bg-orange-50 text-orange-950 dark:bg-orange-950/40 dark:text-orange-100" aria-labelledby="flag-banner-t" data-testid="flag-banner">
      <div className="mx-auto flex max-w-screen-2xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
        <h2 id="flag-banner-t" className="text-sm font-black tracking-wide">
          <span className="mr-2 rounded bg-orange-brand px-2 py-0.5 text-xs tracking-widest text-navy-950">FLAGGED</span>
          Watched activity on your network
        </h2>
        <ul className="flex flex-wrap gap-2 text-sm">
          {active.slice(0, 6).map((f) => (
            <li key={f.id} className="rounded-md bg-white/70 px-2 py-1 dark:bg-navy-900/70" data-testid="flag-item">
              <strong>{f.label || f.hostname || f.client_ip}</strong> · {categoryText(f.category)} · {f.hits} {f.hits === 1 ? 'hit' : 'hits'}
              <span className="sr-only"> (latest {f.qname})</span>
            </li>
          ))}
          {active.length > 6 ? <li className="px-2 py-1">and {active.length - 6} more</li> : null}
        </ul>
        {onShow ? (
          <button type="button" className="btn-secondary ml-auto" onClick={onShow}>
            Show activity
          </button>
        ) : null}
      </div>
    </section>
  );
}
