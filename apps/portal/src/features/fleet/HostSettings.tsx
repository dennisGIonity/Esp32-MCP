/*
 * AEDI - IONITY GLOBAL | @ionity/portal | HostSettings.tsx | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
 *
 * Which fleet host the portal talks to, and the admin token for changes
 * (actions, watchlist edits). The token is kept in memory for this tab only.
 */
import { useEffect, useId, useState } from 'react';
import { normalizeApiBase } from '../../lib/host/verify.ts';
import { useHost } from './HostContext.tsx';

const STATE_TEXT = { connecting: 'connecting…', open: 'live', retrying: 'reconnecting', closed: 'not connected' } as const;

export function HostSettings() {
  const host = useHost();
  const id = useId();
  const [draft, setDraft] = useState(host.base);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<string | null>(null);
  useEffect(() => setDraft(host.base), [host.base]);

  useEffect(() => {
    if (!host.configured || host.blocked) {
      setHealth(null);
      return;
    }
    let cancelled = false;
    host.api.health().then(
      (h) => {
        if (cancelled) return;
        const n = Number(h['devices_known'] ?? Number.NaN);
        setHealth(`fleet "${String(h['fleet'] ?? 'ionity')}" · ${Number.isFinite(n) ? `${n} ${n === 1 ? 'device' : 'devices'}` : 'devices'} known`);
      },
      (e: Error) => !cancelled && setHealth(`not reachable: ${e.message}`),
    );
    return () => {
      cancelled = true;
    };
  }, [host.api, host.configured, host.blocked]);

  function apply(): void {
    const r = normalizeApiBase(draft);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    setError(null);
    host.setBase(r.value);
  }

  return (
    <section className="card flex flex-col gap-3" aria-labelledby={`${id}-t`} data-testid="host-settings">
      <h2 id={`${id}-t`} className="text-lg font-bold text-navy-900 dark:text-slate-100">
        Fleet host
      </h2>
      <form
        className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          apply();
        }}
      >
        <label className="flex flex-col gap-1 text-sm font-semibold">
          Host API address
          <input
            className="field-input font-mono"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="http://192.168.0.3:8099"
            inputMode="url"
            autoComplete="url"
            aria-invalid={error ? 'true' : undefined}
            aria-describedby={error ? `${id}-e` : undefined}
            data-testid="host-base"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm font-semibold">
          Admin token <span className="font-normal text-slate-600 dark:text-slate-400">(kept in this tab only)</span>
          <input
            className="field-input font-mono"
            type="password"
            value={host.adminToken}
            onChange={(e) => host.setAdminToken(e.target.value.trim())}
            autoComplete="off"
            data-testid="host-token"
          />
        </label>
        <button type="submit" className="btn-primary">
          Use this host
        </button>
      </form>
      {error ? (
        <p id={`${id}-e`} className="text-sm font-semibold text-bad">
          {error}
        </p>
      ) : null}
      {host.blocked ? <p className="text-sm font-semibold text-bad">{host.blocked}</p> : null}
      <p className="text-sm text-slate-700 dark:text-slate-300" data-testid="host-status">
        {host.configured ? (
          <>
            {health ?? 'checking…'} · devices {STATE_TEXT[host.devicesState]} · DNS feed {STATE_TEXT[host.liveState]}
            {host.liveDetail && host.liveState === 'retrying' ? ` (${host.liveDetail})` : ''}
          </>
        ) : (
          'No fleet host set. Enter the address of your fleet host (the server that runs the MCP endpoint).'
        )}
      </p>
    </section>
  );
}
