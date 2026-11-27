/*
 * AEDI - IONITY GLOBAL | @ionity/portal | BoardCard.tsx | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * What the board reports about itself. Secrets are only ever shown as
 * "stored" / "not set" - the firmware never returns their values.
 *
 * Two views of a board:
 *   BoardCard       the board on the USB cable (+ its fleet status when the host lists it)
 *   FleetBoardCard  a board as the fleet host sees it (online, RSSI, heap, uptime,
 *                   firmware, DNS forwarder) with quick actions
 */
import type { BoardInfo } from '@ionity/prov';
import { useState } from 'react';
import { checkPingTarget, fmtBytes, fmtUptime, fmtVersion, metric, pingSummary, type FleetDevice, type NodeActionName } from '../../lib/api/client.ts';
import { ConfirmDialog } from '../../ui/ConfirmDialog.tsx';
import { useAnnounce } from '../../ui/announcer.tsx';
import { useFleetDevice, useHost } from '../fleet/HostContext.tsx';

interface Props {
  readonly board: BoardInfo;
  readonly busy: boolean;
  readonly onFactoryReset: () => Promise<void>;
}

export function BoardCard({ board, busy, onFactoryReset }: Props) {
  const [confirm, setConfirm] = useState(false);
  const fleet = useFleetDevice(board.hwid);
  const rows: [string, string][] = [
    ['Chip', [board.chip, board.chipRev !== undefined ? `rev ${board.chipRev}` : '', board.flashMb ? `${board.flashMb} MB flash` : ''].filter(Boolean).join(' · ') || 'unknown'],
    ['Firmware', `${board.fw ?? 'unknown'} · provisioning ${board.prov === 2 ? 'v2 (CRC-checked)' : 'v1 (firmware 2.x)'}`],
    ['Hardware ID', board.hwid],
    ['Device UUID', board.deviceUuid ?? (board.prov === 2 ? 'not set' : 'not supported by firmware 2.x')],
    ['Wi-Fi', board.ssid ? `${board.ssid} · ${board.wifi === 'connected' ? `connected${board.ip ? `, ${board.ip}` : ''}${board.rssi !== undefined ? `, ${board.rssi} dBm` : ''}` : board.wifi === 'down' ? 'not connected' : 'unknown'}` : 'not provisioned'],
    ['Wi-Fi password', board.hasPass ? 'stored' : 'not set'],
    ['MCP host', `${board.hostUrl || 'auto (ionity-fleet.local)'}${board.mqtt === true ? ' · session up' : board.mqtt === false ? ' · not connected' : ''}`],
    ['Fleet', [board.role, board.site && `site ${board.site}`, board.group && `group ${board.group}`, board.label && `“${board.label}”`].filter(Boolean).join(' · ') || '-'],
    ['Secrets', [`MQTT password ${board.hasMqttPass ? 'stored' : 'not set'}`, `MCP token ${board.hasMcpToken ? 'stored' : 'not set'}`, board.prov === 2 ? `CA ${board.hasCa ? 'stored' : 'built-in bundle'}` : ''].filter(Boolean).join(' · ')],
  ];
  if (board.boardMcpUrl) rows.push(['Board MCP endpoint', board.boardMcpUrl]);

  return (
    <section className="card flex flex-col gap-3" aria-labelledby="board-title" data-testid="board-card" data-prov={board.prov}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="board-title" className="text-lg font-bold text-navy-900 dark:text-slate-100">
          Board
        </h2>
        <button type="button" className="btn-secondary" onClick={() => setConfirm(true)} disabled={busy}>
          Factory reset…
        </button>
      </div>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="font-semibold text-slate-600 dark:text-slate-300">{k}</dt>
            <dd className="mb-1 break-words sm:mb-0">{v}</dd>
          </div>
        ))}
      </dl>
      {fleet ? (
        <div className="border-t border-slate-200 pt-3 dark:border-navy-800" data-testid="board-fleet-status">
          <h3 className="mb-2 text-sm font-bold text-navy-900 dark:text-slate-100">On the fleet host</h3>
          <FleetFacts d={fleet} />
        </div>
      ) : null}
      <ConfirmDialog
        open={confirm}
        title="Factory reset this board?"
        confirmLabel="Erase settings"
        onCancel={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false);
          void onFactoryReset();
        }}
      >
        <p>
          Board <strong>{board.hwid}</strong> forgets its Wi-Fi, MCP host, device UUID, tokens and fleet settings, then restarts. Its firmware stays. This cannot be undone.
        </p>
      </ConfirmDialog>
    </section>
  );
}

// ---- fleet status ---------------------------------------------------------------------

const HEALTH_STYLE: Record<FleetDevice['health'], string> = {
  online: 'bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-100',
  alerting: 'bg-orange-100 text-orange-950 dark:bg-orange-950 dark:text-orange-100',
  stale: 'bg-amber-100 text-amber-950 dark:bg-amber-950 dark:text-amber-100',
  offline: 'bg-slate-200 text-slate-800 dark:bg-navy-800 dark:text-slate-200',
};

export function HealthBadge({ health }: { readonly health: FleetDevice['health'] }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-bold uppercase tracking-wide ${HEALTH_STYLE[health]}`} data-testid="health">
      {health}
    </span>
  );
}

export function fleetFacts(d: FleetDevice): [string, string][] {
  const rssi = metric(d, 'rssi_dbm', 'rssi', 'wifi_rssi');
  const heap = metric(d, 'free_heap_bytes', 'free_heap', 'heap_free');
  const minHeap = metric(d, 'min_free_heap_bytes', 'min_free_heap');
  const rows: [string, string][] = [
    ['Status', `${d.health}${d.last_seen_age_s !== null && d.health !== 'online' ? ` · last seen ${fmtUptime(d.last_seen_age_s)} ago` : ''}`],
    ['Wi-Fi signal', rssi === null ? '-' : `${rssi} dBm${rssi >= -60 ? ' (good)' : rssi >= -72 ? ' (fair)' : ' (weak)'}`],
    ['Free heap', heap === null ? '-' : `${fmtBytes(heap)}${minHeap !== null ? ` (lowest ${fmtBytes(minHeap)})` : ''}`],
    ['Uptime', fmtUptime(d.uptime_s)],
    ['Firmware', [d.fw ?? 'unknown', d.protocol === 'ewp1' ? 'EWP/1' : 'JSON (fw 2.x)', d.chip].filter(Boolean).join(' · ')],
    ['Address', [d.ip, d.mac, d.transport].filter(Boolean).join(' · ') || '-'],
  ];
  if (d.dns) {
    const q = d.dns['queries'] ?? d.dns['forwarded'];
    rows.push(['DNS forwarder', `${q ?? 0} queries · ${d.dns['flagged'] ?? 0} flagged · watchlist ${fmtVersion(d.watchlist_version)}`]);
  }
  if (d.active_alerts.length) rows.push(['Alerts', d.active_alerts.join(', ')]);
  return rows;
}

function FleetFacts({ d }: { readonly d: FleetDevice }) {
  return (
    <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
      {fleetFacts(d).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="font-semibold text-slate-600 dark:text-slate-300">{k}</dt>
          <dd className="mb-1 break-words sm:mb-0">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

const ACTION_TEXT: Partial<Record<NodeActionName, string>> = { ping: 'Ping', identify: 'Identify', sync_watchlist: 'Sync watchlist' };

/** ping_host (EWP 0x47): ICMP sent BY the board - "is the board offline, or can it not reach X?" */
function PingFromBoard({ d, name }: { readonly d: FleetDevice; readonly name: string }) {
  const host = useHost();
  const announce = useAnnounce();
  const [target, setTarget] = useState('');
  const [count, setCount] = useState(4);
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<{ ok: boolean; text: string } | null>(null);
  const formId = `ping-${d.device_id}`;
  const problem = target.trim() ? checkPingTarget(target) : null;

  async function go(): Promise<void> {
    const err = checkPingTarget(target);
    if (err) {
      setOut({ ok: false, text: err });
      return;
    }
    setBusy(true);
    setOut(null);
    try {
      const r = await host.api.pingFromNode(d.device_id, target, { count });
      const ok = r.ok !== false && r.reachable === true;
      const text = pingSummary(r);
      setOut({ ok, text });
      announce(`${name}: ping ${text}`, ok ? undefined : { assertive: true });
    } catch (e) {
      const text = (e as Error).message;
      setOut({ ok: false, text });
      announce(`${name}: ping failed, ${text}`, { assertive: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="flex flex-col gap-2 border-t border-slate-200 pt-3 dark:border-slate-700"
      aria-labelledby={`${formId}-h`}
      onSubmit={(e) => {
        e.preventDefault();
        void go();
      }}
      data-testid="ping-from-board"
    >
      <h4 id={`${formId}-h`} className="text-sm font-semibold">Ping from this board</h4>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col text-sm" htmlFor={`${formId}-target`}>
          Host or IP
          <input
            id={`${formId}-target`}
            className="field-input"
            value={target}
            placeholder="192.168.0.1 or youtube.com"
            autoComplete="off"
            spellCheck={false}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${formId}-err` : undefined}
            onChange={(e) => setTarget(e.target.value)}
          />
        </label>
        <label className="flex flex-col text-sm" htmlFor={`${formId}-count`}>
          Echoes
          <input
            id={`${formId}-count`}
            className="field-input w-20"
            type="number"
            min={1}
            max={10}
            value={count}
            onChange={(e) => setCount(Math.min(10, Math.max(1, Number(e.target.value) || 1)))}
          />
        </label>
        <button type="submit" className="btn-secondary" disabled={busy || !target.trim() || problem !== null}>
          {busy ? 'Pinging…' : 'Ping host'}
        </button>
      </div>
      {problem ? (
        <p id={`${formId}-err`} className="text-sm text-bad">
          {problem}
        </p>
      ) : null}
      {out ? (
        <p className={`text-sm font-semibold ${out.ok ? 'text-ok' : 'text-bad'}`} data-testid="ping-result" role="status">
          {out.text}
        </p>
      ) : null}
    </form>
  );
}

export function FleetBoardCard({ d }: { readonly d: FleetDevice }) {
  const host = useHost();
  const announce = useAnnounce();
  const [pending, setPending] = useState<NodeActionName | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [confirmReboot, setConfirmReboot] = useState(false);
  const name = d.label || d.device_id;
  const ewp = d.protocol === 'ewp1';

  async function run(action: NodeActionName): Promise<void> {
    setPending(action);
    setResult(null);
    try {
      const r = await host.api.nodeAction(d.device_id, action);
      const ok = r.ok !== false;
      const text = ok
        ? action === 'ping'
          ? `answered in ${String(r['rtt_ms'] ?? '?')} ms`
          : action === 'reboot'
            ? 'restarting'
            : action === 'sync_watchlist'
              ? 'watchlist sent'
              : 'done'
        : String(r.error ?? 'failed');
      setResult({ ok, text });
      announce(`${name}: ${ACTION_TEXT[action] ?? action} ${ok ? text : `failed, ${text}`}`, ok ? undefined : { assertive: true });
    } catch (e) {
      const text = (e as Error).message;
      setResult({ ok: false, text });
      announce(`${name}: ${action} failed, ${text}`, { assertive: true });
    } finally {
      setPending(null);
    }
  }

  const needsAdmin = !host.adminToken;
  return (
    <article className="card flex flex-col gap-3" aria-labelledby={`fleet-${d.device_id}`} data-testid="fleet-card" data-device={d.device_id} data-health={d.health}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={`fleet-${d.device_id}`} className="text-base font-bold text-navy-900 dark:text-slate-100">
          {name}
          {d.label ? <span className="ml-2 font-mono text-xs font-normal text-slate-600 dark:text-slate-400">{d.device_id}</span> : null}
        </h3>
        <HealthBadge health={d.health} />
      </div>
      <FleetFacts d={d} />
      <div className="flex flex-wrap gap-2" role="group" aria-label={`Actions for ${name}`}>
        <button type="button" className="btn-secondary" disabled={pending !== null} onClick={() => void run('ping')}>
          {pending === 'ping' ? 'Pinging…' : 'Ping'}
        </button>
        {ewp ? (
          <button type="button" className="btn-secondary" disabled={pending !== null} onClick={() => void run('identify')}>
            Identify
          </button>
        ) : null}
        {ewp && d.dns ? (
          <button
            type="button"
            className="btn-secondary"
            disabled={pending !== null || needsAdmin}
            title={needsAdmin ? 'needs the admin token' : undefined}
            onClick={() => void run('sync_watchlist')}
          >
            {pending === 'sync_watchlist' ? 'Syncing…' : 'Sync watchlist'}
          </button>
        ) : null}
        <button
          type="button"
          className="btn-secondary"
          disabled={pending !== null || needsAdmin}
          title={needsAdmin ? 'needs the admin token' : undefined}
          onClick={() => setConfirmReboot(true)}
        >
          Reboot…
        </button>
      </div>
      {result ? (
        <p className={`text-sm font-semibold ${result.ok ? 'text-ok' : 'text-bad'}`} data-testid="action-result">
          {result.text}
        </p>
      ) : null}
      {ewp && d.tools.includes('ping_host') ? <PingFromBoard d={d} name={name} /> : null}
      <ConfirmDialog
        open={confirmReboot}
        title={`Reboot ${name}?`}
        confirmLabel="Reboot"
        onCancel={() => setConfirmReboot(false)}
        onConfirm={() => {
          setConfirmReboot(false);
          void run('reboot');
        }}
      >
        <p>
          The board restarts and is offline for a few seconds. {d.dns ? 'Devices that use it as their DNS server lose name lookups until it is back.' : ''}
        </p>
      </ConfirmDialog>
    </article>
  );
}
