/*
 * AEDI - IONITY GLOBAL | @ionity/portal | FleetPanel.tsx | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
 *
 * Fleet & node status: every board the host knows, live from /ws/fleet.
 */
import { useMemo, useState } from 'react';
import { FleetBoardCard } from '../device/BoardCard.tsx';
import { useHost } from './HostContext.tsx';

const ORDER = { alerting: 0, online: 1, stale: 2, offline: 3 } as const;

export function FleetPanel() {
  const host = useHost();
  const [search, setSearch] = useState('');
  const [onlyOnline, setOnlyOnline] = useState(false);

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return [...host.devices]
      .filter((d) => !onlyOnline || d.health === 'online' || d.health === 'alerting')
      .filter((d) => !q || [d.device_id, d.label, d.ip, d.mac, d.site, d.group].some((v) => v?.toLowerCase().includes(q)))
      .sort((a, b) => ORDER[a.health] - ORDER[b.health] || (a.label ?? a.device_id).localeCompare(b.label ?? b.device_id));
  }, [host.devices, search, onlyOnline]);

  const online = host.devices.filter((d) => d.health === 'online' || d.health === 'alerting').length;

  return (
    <section className="flex flex-col gap-4" aria-labelledby="fleet-title" data-testid="fleet-panel">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="fleet-title" className="text-lg font-bold text-navy-900 dark:text-slate-100">
            Fleet &amp; node status
          </h2>
          <p className="text-sm text-slate-700 dark:text-slate-300" data-testid="fleet-count">
            {host.devices.length} boards · {online} online
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm font-semibold">
            Find a board
            <input className="field-input" type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="name, ID, IP or MAC" />
          </label>
          <label className="flex min-h-11 items-center gap-2 text-sm font-semibold">
            <input type="checkbox" className="size-5" checked={onlyOnline} onChange={(e) => setOnlyOnline(e.target.checked)} />
            Online only
          </label>
        </div>
      </div>
      {!host.configured ? (
        <p className="card text-sm">Set the fleet host above to see your boards.</p>
      ) : host.devices.length === 0 ? (
        <p className="card text-sm" data-testid="fleet-empty">
          {host.devicesState === 'open' ? 'The host does not know any boards yet. Provision one on the Board tab.' : 'Connecting to the fleet host…'}
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {shown.map((d) => (
            <FleetBoardCard key={d.device_id} d={d} />
          ))}
        </div>
      )}
    </section>
  );
}
