/*
 * AEDI - IONITY GLOBAL | @ionity/portal | ConnectionBar.tsx | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Connect / disconnect / reset, and the state of the serial link in words.
 * "Connect board" must run inside the click handler: requestPort() needs a
 * user gesture (transient activation).
 */
import type { SessionStatus } from '../../lib/serial/session.ts';
import { hex4 } from '../../lib/serial/usb.ts';
import type { Device } from './useDevice.ts';

const STATUS_TEXT: Record<SessionStatus, string> = {
  idle: 'Not connected',
  opening: 'Opening the port…',
  open: 'Connected',
  leased: 'Port in use by the firmware installer',
  detached: 'Board left USB - waiting for it to return',
  closing: 'Closing…',
  error: 'Connection problem',
};

const DOT: Record<SessionStatus, string> = {
  idle: 'bg-slate-400',
  opening: 'bg-amber-400',
  open: 'bg-ok',
  leased: 'bg-cyan-brand',
  detached: 'bg-amber-500',
  closing: 'bg-slate-400',
  error: 'bg-bad',
};

export function ConnectionBar({ device, busy }: { readonly device: Device; readonly busy: boolean }) {
  const { snapshot, session } = device;
  const st = snapshot.status;
  const port = snapshot.port;
  const linked = st === 'open' || st === 'detached' || st === 'opening';

  return (
    <section className="card flex flex-col gap-3" aria-labelledby="connection-title" data-testid="connection">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 id="connection-title" className="text-lg font-bold text-navy-900 dark:text-slate-100">
            Connection
          </h2>
          <p className="flex items-center gap-2 text-sm" data-testid="connection-status" data-status={st}>
            <span aria-hidden="true" className={`inline-block size-2.5 rounded-full ${DOT[st]}`} />
            <span>
              {STATUS_TEXT[st]}
              {port && st !== 'idle' ? ` · ${port.bridge} (${hex4(port.vid)}:${hex4(port.pid)})` : ''}
              {snapshot.reconnects > 0 && st === 'open' ? ` · reattached ${snapshot.reconnects}×` : ''}
            </span>
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {!linked ? (
            <button type="button" className="btn-primary" onClick={() => void device.connect()} disabled={!session.supported || st === 'leased' || st === 'closing'}>
              Connect board
            </button>
          ) : (
            <button type="button" className="btn-secondary" onClick={() => void device.disconnect()} disabled={busy}>
              Disconnect
            </button>
          )}
          {st === 'open' ? (
            <button type="button" className="btn-secondary" onClick={() => void device.resetBoard()} disabled={busy}>
              Reset board
            </button>
          ) : null}
          {st === 'open' && !device.board ? (
            <button type="button" className="btn-secondary" onClick={() => void device.identify()} disabled={busy || device.identifying}>
              {device.identifying ? 'Identifying…' : 'Identify'}
            </button>
          ) : null}
        </div>
      </div>
      {snapshot.error ? (
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-900 dark:bg-red-950/40 dark:text-red-100" data-testid="connection-error">
          {snapshot.error}
        </p>
      ) : null}
      {device.identifyError && st === 'open' && !device.board ? (
        <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-950 dark:bg-amber-950/40 dark:text-amber-100">
          The board is connected but did not identify itself. It may run other firmware (flash it below), still be booting, or be a native-USB board that needs one press of RESET.
        </p>
      ) : null}
    </section>
  );
}
