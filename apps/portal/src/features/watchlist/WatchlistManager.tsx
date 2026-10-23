/*
 * AEDI - IONITY GLOBAL | @ionity/portal | WatchlistManager.tsx | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
 *
 * The DNS watchlist: categories ("video_streaming:youtube") and domain rules
 * ("*.googlevideo.com"). The host is authoritative; every change bumps the
 * version and the host pushes it to the NetObserve nodes, which flag at the
 * edge. Built-in rules can be switched off but not deleted. Changes need the
 * admin token.
 */
import { useCallback, useEffect, useId, useState } from 'react';
import { fmtVersion, type Watchlist } from '../../lib/api/client.ts';
import { checkCategory, checkPattern } from '../../lib/api/watchlist.ts';
import { ConfirmDialog } from '../../ui/ConfirmDialog.tsx';
import { useAnnounce } from '../../ui/announcer.tsx';
import { useHost } from '../fleet/HostContext.tsx';
import { categoryText } from '../network/FlaggedBanner.tsx';

export function WatchlistManager() {
  const host = useHost();
  const announce = useAnnounce();
  const id = useId();
  const [wl, setWl] = useState<Watchlist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pattern, setPattern] = useState('');
  const [category, setCategory] = useState('video_streaming:youtube');
  const [alert, setAlert] = useState(true);
  const [fieldError, setFieldError] = useState<{ pattern?: string; category?: string }>({});
  const [newCat, setNewCat] = useState({ name: '', label: '' });
  const [removeCat, setRemoveCat] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!host.configured) return;
    try {
      setWl(await host.api.watchlist());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [host.api, host.configured]);

  useEffect(() => {
    void load();
  }, [load]);
  // another viewer (or the MCP tool) changed the watchlist
  useEffect(() => {
    if (wl && host.live.watchlistVersion !== null && host.live.watchlistVersion !== wl.version) void load();
  }, [host.live.watchlistVersion, wl, load]);

  async function change(what: string, fn: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    try {
      await fn();
      await load();
      announce(what);
      return true;
    } catch (e) {
      const msg = `${what.replace(/\.$/, '')} failed: ${(e as Error).message}`;
      setError(msg);
      announce(msg, { assertive: true });
      return false;
    } finally {
      setBusy(false);
    }
  }

  const readOnly = !host.adminToken;
  const cats = wl?.categories ?? [];

  return (
    <section className="flex flex-col gap-4" aria-labelledby={`${id}-t`} data-testid="watchlist">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id={`${id}-t`} className="text-lg font-bold text-navy-900 dark:text-slate-100">
            DNS watchlist
          </h2>
          <p className="text-sm text-slate-700 dark:text-slate-300" data-testid="watchlist-version">
            Version {fmtVersion(wl?.version)} · {wl?.rules.filter((r) => r.enabled).length ?? 0} active rules
            {readOnly ? ' · enter the admin token to make changes' : ''}
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary"
          disabled={readOnly || busy || !wl}
          onClick={() => void change('Watchlist sent to the nodes.', () => host.api.syncWatchlist())}
        >
          Push to nodes now
        </button>
      </div>
      {error ? (
        <p className="text-sm font-semibold text-bad" role="alert">
          {error}
        </p>
      ) : null}

      {wl?.nodes.length ? (
        <ul className="flex flex-wrap gap-2 text-sm" aria-label="Nodes">
          {wl.nodes.map((n) => (
            <li key={n.device_id} className={`rounded-md px-2 py-1 ${n.in_sync ? 'bg-green-100 dark:bg-green-950' : 'bg-amber-100 dark:bg-amber-950'}`} data-testid="wl-node">
              {n.label || n.device_id}: {n.in_sync ? 'up to date' : `has ${fmtVersion(n.version)}`}
            </li>
          ))}
        </ul>
      ) : null}

      <form
        className="card grid gap-3 sm:grid-cols-[1.4fr_1fr_auto_auto] sm:items-start"
        aria-label="Add a rule"
        onSubmit={(e) => {
          e.preventDefault();
          const p = checkPattern(pattern);
          const c = checkCategory(category);
          setFieldError({ ...(p.ok ? {} : { pattern: p.error }), ...(c.ok ? {} : { category: c.error }) });
          if (!p.ok || !c.ok) return;
          void change(`Rule ${p.value} added.`, () => host.api.addRule(p.value, c.value, alert)).then((ok) => ok && setPattern(''));
        }}
      >
        <label className="flex flex-col gap-1 text-sm font-semibold">
          Domain
          <input
            className="field-input font-mono"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            placeholder="*.example.com"
            aria-invalid={fieldError.pattern ? 'true' : undefined}
            aria-describedby={fieldError.pattern ? `${id}-pe` : undefined}
            data-testid="rule-pattern"
          />
          {fieldError.pattern ? (
            <span id={`${id}-pe`} className="font-normal text-bad">
              {fieldError.pattern}
            </span>
          ) : null}
        </label>
        <label className="flex flex-col gap-1 text-sm font-semibold">
          Category
          <input
            className="field-input font-mono"
            list={`${id}-cats`}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            aria-invalid={fieldError.category ? 'true' : undefined}
            aria-describedby={fieldError.category ? `${id}-ce` : undefined}
            data-testid="rule-category"
          />
          <datalist id={`${id}-cats`}>
            {cats.map((c) => (
              <option key={c.name} value={c.name}>
                {c.label}
              </option>
            ))}
          </datalist>
          {fieldError.category ? (
            <span id={`${id}-ce`} className="font-normal text-bad">
              {fieldError.category}
            </span>
          ) : null}
        </label>
        <label className="flex min-h-11 items-center gap-2 text-sm font-semibold sm:mt-6">
          <input type="checkbox" className="size-5" checked={alert} onChange={(e) => setAlert(e.target.checked)} />
          Alert
        </label>
        <button type="submit" className="btn-primary sm:mt-6" disabled={readOnly || busy} data-testid="rule-add">
          Add rule
        </button>
      </form>

      <div className="overflow-auto rounded-lg border border-slate-200 dark:border-navy-800">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Watchlist rules</caption>
          <thead className="bg-slate-100 text-xs uppercase tracking-wide dark:bg-navy-800">
            <tr>
              <th scope="col" className="px-2 py-2">
                Domain
              </th>
              <th scope="col" className="px-2 py-2">
                Category
              </th>
              <th scope="col" className="px-2 py-2">
                Alert
              </th>
              <th scope="col" className="px-2 py-2 text-right">
                Hits
              </th>
              <th scope="col" className="px-2 py-2">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-navy-800">
            {(wl?.rules ?? []).map((r) => (
              <tr key={r.id} className={r.enabled ? '' : 'text-slate-500'} data-testid="rule-row" data-pattern={r.pattern}>
                <td className="px-2 py-1 font-mono">
                  {r.pattern}
                  {r.builtin ? <span className="ml-2 rounded bg-slate-200 px-1 text-xs dark:bg-navy-800">built-in</span> : null}
                </td>
                <td className="px-2 py-1">{categoryText(r.category)}</td>
                <td className="px-2 py-1">{r.alert ? 'yes' : 'log only'}</td>
                <td className="px-2 py-1 text-right">{r.hits ?? 0}</td>
                <td className="flex flex-wrap justify-end gap-2 px-2 py-1">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      className="size-5"
                      checked={r.enabled}
                      disabled={readOnly || busy}
                      onChange={(e) => void change(`Rule ${r.pattern} ${e.target.checked ? 'on' : 'off'}.`, () => host.api.setRuleEnabled(r.id, e.target.checked))}
                    />
                    <span className="sr-only">Rule {r.pattern} </span>On
                  </label>
                  {!r.builtin ? (
                    <button
                      type="button"
                      className="btn-secondary !min-h-9 !px-2 !py-1"
                      disabled={readOnly || busy}
                      aria-label={`Delete rule ${r.pattern}`}
                      onClick={() => void change(`Rule ${r.pattern} deleted.`, () => host.api.deleteRule(r.id))}
                    >
                      Delete
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <details className="card">
        <summary className="cursor-pointer font-semibold">Categories ({cats.length})</summary>
        <ul className="mt-3 flex flex-col gap-2 text-sm">
          {cats.map((c) => (
            <li key={c.name} className="flex flex-wrap items-center justify-between gap-2">
              <span>
                <span className="font-mono">{c.name}</span> - {c.label} · {c.rules ?? 0} rules{c.builtin ? ' · built-in' : ''}
              </span>
              {!c.builtin ? (
                <button type="button" className="btn-secondary" disabled={readOnly || busy} onClick={() => setRemoveCat(c.name)}>
                  Delete…
                </button>
              ) : null}
            </li>
          ))}
        </ul>
        <form
          className="mt-3 grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
          aria-label="Add a category"
          onSubmit={(e) => {
            e.preventDefault();
            const c = checkCategory(newCat.name);
            if (!c.ok) {
              setError(c.error);
              return;
            }
            void change(`Category ${c.value} added.`, () => host.api.addCategory(c.value, newCat.label.trim() || c.value)).then((ok) => ok && setNewCat({ name: '', label: '' }));
          }}
        >
          <label className="flex flex-col gap-1 text-sm font-semibold">
            Name
            <input className="field-input font-mono" value={newCat.name} onChange={(e) => setNewCat((n) => ({ ...n, name: e.target.value }))} placeholder="social:instagram" />
          </label>
          <label className="flex flex-col gap-1 text-sm font-semibold">
            Label
            <input className="field-input" value={newCat.label} onChange={(e) => setNewCat((n) => ({ ...n, label: e.target.value }))} placeholder="Instagram" />
          </label>
          <button type="submit" className="btn-secondary" disabled={readOnly || busy}>
            Add category
          </button>
        </form>
      </details>

      <ConfirmDialog
        open={removeCat !== null}
        title={`Delete category ${removeCat ?? ''}?`}
        confirmLabel="Delete category"
        onCancel={() => setRemoveCat(null)}
        onConfirm={() => {
          const name = removeCat;
          setRemoveCat(null);
          if (name) void change(`Category ${name} deleted.`, () => host.api.deleteCategory(name));
        }}
      >
        <p>Only an empty category can be deleted: remove its rules first.</p>
      </ConfirmDialog>
    </section>
  );
}
