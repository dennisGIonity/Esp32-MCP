/*
 * AEDI - IONITY GLOBAL | @ionity/portal | BoardCard.tsx | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * What the board reports about itself. Secrets are only ever shown as
 * "stored" / "not set" - the firmware never returns their values.
 */
import type { BoardInfo } from '@ionity/prov';
import { useState } from 'react';
import { ConfirmDialog } from '../../ui/ConfirmDialog.tsx';

interface Props {
  readonly board: BoardInfo;
  readonly busy: boolean;
  readonly onFactoryReset: () => Promise<void>;
}

export function BoardCard({ board, busy, onFactoryReset }: Props) {
  const [confirm, setConfirm] = useState(false);
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
