/*
 * AEDI - IONITY GLOBAL | @ionity/portal | FlashPanel.tsx | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Install firmware over the same serial connection (esptool-js via
 * SerialSession.lease). The release comes from a manifest (SHA-256 checked) or
 * a local .bin. "Keep board settings" is on by default; erasing everything asks
 * for confirmation first.
 */
import { useEffect, useId, useState } from 'react';
import type { Manifest } from '../../lib/flash/manifest.ts';
import { prefs } from '../../lib/store.ts';
import { ConfirmDialog } from '../../ui/ConfirmDialog.tsx';
import { useAnnounce } from '../../ui/announcer.tsx';
import type { Device } from '../device/useDevice.ts';

interface Props {
  readonly device: Device;
  readonly busy: boolean;
  readonly onBusyChange: (busy: boolean) => void;
  /** the simulator has no ROM bootloader to talk to */
  readonly simulated: boolean;
}

type Source = 'release' | 'file';

function defaultManifestUrl(): string {
  return new URL('firmware/manifest.json', document.baseURI).href;
}

export function FlashPanel({ device, busy, onBusyChange, simulated }: Props) {
  const announce = useAnnounce();
  const uid = useId();
  const [source, setSource] = useState<Source>('release');
  const [manifestUrl] = useState(() => prefs.get('firmwareUrl') || defaultManifestUrl());
  const [manifest, setManifest] = useState<{ value: Manifest | null; error: string | null }>({ value: null, error: null });
  const [file, setFile] = useState<{ name: string; data: Uint8Array } | null>(null);
  const [address, setAddress] = useState('0x0');
  const [keep, setKeep] = useState(true);
  const [eraseAll, setEraseAll] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [progress, setProgress] = useState<{ stage: string; pct: number | null; error: string | null; done: string | null }>({ stage: '', pct: null, error: null, done: null });
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (source !== 'release' || simulated) return;
    let cancelled = false;
    void import('../../lib/flash/installer.ts')
      .then(({ fetchManifest }) => fetchManifest(manifestUrl))
      .then(
        (m) => !cancelled && setManifest({ value: m, error: null }),
        (e: unknown) => !cancelled && setManifest({ value: null, error: (e as Error).message }),
      );
    return () => {
      cancelled = true;
    };
  }, [source, manifestUrl, simulated]);

  const connected = device.snapshot.status === 'open';
  const addr = Number.parseInt(address, address.trim().toLowerCase().startsWith('0x') ? 16 : 10);
  const addrValid = Number.isInteger(addr) && addr >= 0 && addr % 0x1000 === 0;
  const ready = connected && !busy && !simulated && (source === 'release' ? manifest.value !== null : file !== null && addrValid);

  async function install(): Promise<void> {
    setConfirm(false);
    setRunning(true);
    onBusyChange(true);
    setProgress({ stage: 'Starting…', pct: null, error: null, done: null });
    try {
      const { installFirmware } = await import('../../lib/flash/installer.ts');
      const src =
        source === 'release' && manifest.value
          ? ({ kind: 'manifest', manifest: manifest.value, baseUrl: manifestUrl } as const)
          : ({ kind: 'file', name: file?.name ?? 'image.bin', data: file?.data ?? new Uint8Array(), offset: addr } as const);
      const r = await installFirmware(device.session, {
        source: src,
        keepSettings: keep,
        eraseAll,
        onStage: (_stage, detail) => {
          setProgress((p) => ({ ...p, stage: detail, pct: null }));
          device.note(detail, 'info');
        },
        onProgress: (pct) => setProgress((p) => ({ ...p, pct })),
        onLog: (text) => device.note(`esptool: ${text}`, 'tx'),
      });
      const msg = `Firmware written to ${r.family}${r.build ? ` (${r.build.file})` : ''}${r.keptSettings ? ', settings kept' : ''}. The board is restarting.`;
      setProgress({ stage: '', pct: 100, error: null, done: msg });
      announce(msg);
    } catch (e) {
      const msg = (e as Error).message;
      setProgress({ stage: '', pct: null, error: msg, done: null });
      device.note(`Install failed: ${msg}`, 'error');
      announce(`Firmware install failed: ${msg}`, { assertive: true });
    } finally {
      setRunning(false);
      onBusyChange(false);
    }
  }

  const fileId = `${uid}-file`;
  const addrId = `${uid}-addr`;
  return (
    <section className="card flex flex-col gap-4" aria-labelledby={`${uid}-title`} data-testid="flash-panel">
      <div>
        <h2 id={`${uid}-title`} className="text-lg font-bold text-navy-900 dark:text-slate-100">
          Firmware
        </h2>
        <p className="text-sm text-slate-600 dark:text-slate-300">Install or update the board&rsquo;s firmware over this USB connection.</p>
      </div>
      {simulated ? (
        <p className="rounded-md bg-slate-100 p-3 text-sm dark:bg-navy-800">The simulated board has no bootloader, so installing firmware is off in simulator mode.</p>
      ) : null}

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-semibold">Image</legend>
        <label className="flex min-h-11 items-center gap-3 text-sm">
          <input type="radio" name={`${uid}-src`} className="size-5" checked={source === 'release'} onChange={() => setSource('release')} />
          <span>
            Release
            {manifest.value ? ` ${manifest.value.version} (${manifest.value.builds.map((b) => b.chip_family).filter((f, i, a) => a.indexOf(f) === i).join(', ')})` : ''}
          </span>
        </label>
        <label className="flex min-h-11 items-center gap-3 text-sm">
          <input type="radio" name={`${uid}-src`} className="size-5" checked={source === 'file'} onChange={() => setSource('file')} />
          <span>A .bin file from this computer</span>
        </label>
      </fieldset>

      {source === 'release' && manifest.error && !simulated ? (
        <p className="text-sm text-amber-900 dark:text-amber-200">No firmware release found at {manifestUrl} ({manifest.error}). Choose a .bin file instead.</p>
      ) : null}

      {source === 'file' ? (
        <div className="grid gap-4 sm:grid-cols-[1fr_10rem]">
          <div className="flex flex-col gap-1">
            <label htmlFor={fileId} className="text-sm font-semibold">
              Image file (.bin)
            </label>
            <input
              id={fileId}
              type="file"
              accept=".bin,application/octet-stream"
              className="text-sm"
              onChange={async (e) => {
                const f = e.currentTarget.files?.[0];
                setFile(f ? { name: f.name, data: new Uint8Array(await f.arrayBuffer()) } : null);
              }}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={addrId} className="text-sm font-semibold">
              Flash address
            </label>
            <input
              id={addrId}
              className="field-input font-mono"
              value={address}
              onChange={(e) => setAddress(e.currentTarget.value)}
              aria-invalid={!addrValid}
              aria-describedby={`${addrId}-hint`}
              spellCheck={false}
            />
            <p id={`${addrId}-hint`} className={`text-xs ${addrValid ? 'text-slate-600 dark:text-slate-300' : 'font-semibold text-bad'}`}>
              {addrValid ? '0x0 for merged images.' : 'Use a 4 KiB-aligned address such as 0x0 or 0x10000.'}
            </p>
          </div>
        </div>
      ) : null}

      <div className="flex flex-col gap-1">
        <label className="flex min-h-11 items-center gap-3 text-sm">
          <input type="checkbox" className="size-5" checked={keep && !eraseAll} disabled={eraseAll} onChange={(e) => setKeep(e.currentTarget.checked)} />
          <span>Keep board settings (Wi-Fi, MCP host, device UUID, tokens)</span>
        </label>
        <label className="flex min-h-11 items-center gap-3 text-sm">
          <input type="checkbox" className="size-5" checked={eraseAll} onChange={(e) => setEraseAll(e.currentTarget.checked)} />
          <span>Erase the whole flash first (removes all settings)</span>
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn-primary" disabled={!ready} onClick={() => (eraseAll ? setConfirm(true) : void install())}>
          {running ? 'Installing…' : 'Install firmware'}
        </button>
        {!connected && !simulated ? <span className="text-sm text-slate-600 dark:text-slate-300">Connect a board first.</span> : null}
      </div>

      {running || progress.done || progress.error ? (
        <div className="flex flex-col gap-2" data-testid="flash-progress">
          {running ? (
            <>
              <label htmlFor={`${uid}-progress`} className="text-sm">
                {progress.stage || 'Working…'}
              </label>
              <progress id={`${uid}-progress`} className="h-3 w-full accent-cyan-brand" max={100} {...(progress.pct !== null ? { value: progress.pct } : {})} />
            </>
          ) : null}
          {progress.done ? <p className="text-sm font-medium text-ok dark:text-green-400">{progress.done}</p> : null}
          {progress.error ? <p className="text-sm font-medium text-bad dark:text-red-300">Install failed: {progress.error}</p> : null}
        </div>
      ) : null}

      <ConfirmDialog open={confirm} title="Erase the whole flash?" confirmLabel="Erase and install" onCancel={() => setConfirm(false)} onConfirm={() => void install()}>
        <p>Everything on the board is erased before the new firmware is written: Wi-Fi, MCP host, device UUID, tokens and any stored data. You will have to provision it again.</p>
      </ConfirmDialog>
    </section>
  );
}
