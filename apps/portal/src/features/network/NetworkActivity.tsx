/*
 * AEDI - IONITY GLOBAL | @ionity/portal | NetworkActivity.tsx | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
 *
 * Live DNS activity: every lookup the NetObserve nodes (and the host's own
 * resolver) forward, attributed to a device name, live over /ws/events.
 * "Search history" runs the same filters against the stored log
 * (GET /api/v1/dns/queries, paginated).
 *
 * Only monitor networks you manage, with the users' knowledge (POPIA).
 */
import { useId, useMemo, useState } from 'react';
import { clientName, qtypeName, type DnsQuery } from '../../lib/api/client.ts';
import { NO_FILTER, filterFeed, type FeedFilter } from '../../lib/api/live.ts';
import { useHost } from '../fleet/HostContext.tsx';
import { categoryText } from './FlaggedBanner.tsx';

const SHOWN = 200;

function time(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour12: false });
}

function Row({ q }: { readonly q: DnsQuery }) {
  const flagged = Boolean(q.flagged);
  return (
    <tr className={flagged ? 'bg-orange-50 dark:bg-orange-950/40' : ''} data-testid="dns-row" data-flagged={flagged ? '1' : '0'}>
      <td className="whitespace-nowrap px-2 py-1 font-mono text-xs">{time(q.ts)}</td>
      <td className="px-2 py-1">
        <span className="font-semibold">{clientName(q)}</span>
        {clientName(q) !== q.client_ip ? <span className="block font-mono text-xs text-slate-600 dark:text-slate-400">{q.client_ip}</span> : null}
      </td>
      <td className="break-all px-2 py-1 font-mono text-xs">{q.qname}</td>
      <td className="px-2 py-1 text-xs">{qtypeName(q.qtype)}</td>
      <td className="px-2 py-1 text-xs">
        {q.category ? (
          <span className={flagged ? 'font-bold text-orange-900 dark:text-orange-200' : ''}>
            {flagged ? 'Flagged · ' : ''}
            {categoryText(q.category)}
          </span>
        ) : (
          '-'
        )}
      </td>
      <td className="whitespace-nowrap px-2 py-1 text-right text-xs">
        {q.count && q.count > 1 ? `×${q.count} ` : ''}
        {q.cached ? 'cache' : q.latency_ms !== null && q.latency_ms !== undefined ? `${Math.round(q.latency_ms)} ms` : ''}
      </td>
    </tr>
  );
}

function Table({ rows, caption }: { readonly rows: readonly DnsQuery[]; readonly caption: string }) {
  return (
    <div className="max-h-[60vh] overflow-auto rounded-lg border border-slate-200 dark:border-navy-800">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 bg-slate-100 text-xs uppercase tracking-wide dark:bg-navy-800">
          <tr>
            <th scope="col" className="px-2 py-2">
              Time
            </th>
            <th scope="col" className="px-2 py-2">
              Device
            </th>
            <th scope="col" className="px-2 py-2">
              Domain
            </th>
            <th scope="col" className="px-2 py-2">
              Type
            </th>
            <th scope="col" className="px-2 py-2">
              Category
            </th>
            <th scope="col" className="px-2 py-2 text-right">
              Answer
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-navy-800">
          {rows.map((q, i) => (
            <Row key={`${q.ts}-${q.client_ip}-${q.qname}-${i}`} q={q} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function NetworkActivity() {
  const host = useHost();
  const id = useId();
  const [filter, setFilter] = useState<FeedFilter>(NO_FILTER);
  const [paused, setPaused] = useState<readonly DnsQuery[] | null>(null);
  const [history, setHistory] = useState<{ rows: DnsQuery[]; next: number | null; loading: boolean; error: string | null } | null>(null);

  const source = paused ?? host.live.dns;
  const rows = useMemo(() => filterFeed(source, filter), [source, filter]);
  const set = <K extends keyof FeedFilter>(k: K, v: FeedFilter[K]) => setFilter((f) => ({ ...f, [k]: v }));

  async function search(offset = 0): Promise<void> {
    setHistory((h) => ({ rows: offset ? (h?.rows ?? []) : [], next: null, loading: true, error: null }));
    try {
      const dev = filter.device.trim();
      const mac = /^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$/i.test(dev) ? dev : undefined;
      const ip = /^\d{1,3}(\.\d{1,3}){3}$/.test(dev) ? dev : undefined;
      const r = await host.api.dnsQueries({
        ...(filter.domain.trim() ? { domain: filter.domain.trim() } : {}),
        ...(mac ? { mac } : ip ? { client_ip: ip } : dev ? { label: dev } : {}),
        flagged_only: filter.flaggedOnly,
        minutes: 24 * 60,
        limit: 100,
        offset,
      });
      setHistory((h) => ({ rows: [...(offset ? (h?.rows ?? []) : []), ...r.queries], next: r.has_more ? r.next_offset : null, loading: false, error: null }));
    } catch (e) {
      setHistory((h) => ({ rows: h?.rows ?? [], next: null, loading: false, error: (e as Error).message }));
    }
  }

  return (
    <section className="flex flex-col gap-4" aria-labelledby={`${id}-t`} data-testid="network-activity">
      <div>
        <h2 id={`${id}-t`} className="text-lg font-bold text-navy-900 dark:text-slate-100">
          Live DNS activity
        </h2>
        <p className="text-sm text-slate-700 dark:text-slate-300">
          Name lookups from devices that use a NetObserve node (or this host) as their DNS server. Only monitor networks you manage, with the users' knowledge.
        </p>
      </div>

      <form
        className="card grid gap-3 sm:grid-cols-[1fr_1fr_auto_auto_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
        aria-label="Filter DNS activity"
      >
        <label className="flex flex-col gap-1 text-sm font-semibold">
          Device
          <input className="field-input" value={filter.device} onChange={(e) => set('device', e.target.value)} placeholder="name, IP or MAC" data-testid="filter-device" />
        </label>
        <label className="flex flex-col gap-1 text-sm font-semibold">
          Domain
          <input className="field-input font-mono" value={filter.domain} onChange={(e) => set('domain', e.target.value)} placeholder="youtube or *.googlevideo.com" data-testid="filter-domain" />
        </label>
        <label className="flex min-h-11 items-center gap-2 text-sm font-semibold">
          <input type="checkbox" className="size-5" checked={filter.flaggedOnly} onChange={(e) => set('flaggedOnly', e.target.checked)} data-testid="filter-flagged" />
          Flagged only
        </label>
        <button type="button" className="btn-secondary" onClick={() => setPaused((p) => (p ? null : host.live.dns))} aria-pressed={paused !== null}>
          {paused ? 'Resume' : 'Pause'}
        </button>
        <button type="submit" className="btn-primary" disabled={!host.configured}>
          Search history
        </button>
      </form>

      <p className="text-sm" data-testid="feed-status">
        {host.liveState === 'open' ? (paused ? 'Paused. ' : 'Live. ') : host.liveState === 'retrying' ? `Reconnecting (${host.liveDetail}). ` : 'Connecting… '}
        Showing {Math.min(rows.length, SHOWN)} of {rows.length} recent lookups{filter.device || filter.domain || filter.flaggedOnly ? ' that match' : ''}.
      </p>
      <Table rows={rows.slice(0, SHOWN)} caption="Recent DNS lookups, newest first" />

      {history ? (
        <section className="flex flex-col gap-2" aria-labelledby={`${id}-h`} data-testid="dns-history">
          <h3 id={`${id}-h`} className="font-bold">
            Stored lookups (last 24 h)
          </h3>
          {history.error ? <p className="text-sm font-semibold text-bad">{history.error}</p> : null}
          {history.rows.length ? <Table rows={history.rows} caption="Stored DNS lookups matching the filter" /> : !history.loading ? <p className="text-sm">Nothing stored matches.</p> : null}
          <div className="flex gap-2">
            {history.next !== null ? (
              <button type="button" className="btn-secondary" disabled={history.loading} onClick={() => void search(history.next ?? 0)}>
                Load more
              </button>
            ) : null}
            <button type="button" className="btn-secondary" onClick={() => setHistory(null)}>
              Close
            </button>
            {history.loading ? <span className="self-center text-sm">Loading…</span> : null}
          </div>
        </section>
      ) : null}
    </section>
  );
}
