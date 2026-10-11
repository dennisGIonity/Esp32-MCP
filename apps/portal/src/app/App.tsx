/*
 * AEDI - IONITY GLOBAL | @ionity/portal | App.tsx | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
 * www.ionity.today | www.ionity.world | Ref: www.ionity.co.za
 */
import { DeviceConsole } from '../features/device/DeviceConsole.tsx';
import type { Simulation } from '../sim/simulator.ts';
import { AnnouncerProvider } from '../ui/announcer.tsx';

export function App({ sim }: { readonly sim: Simulation | null }) {
  const hasSerial = typeof navigator !== 'undefined' && 'serial' in navigator;
  const secure = typeof window !== 'undefined' && window.isSecureContext;

  return (
    <AnnouncerProvider>
      <a
        href="#main"
        className="sr-only rounded-md bg-white px-4 py-2 font-semibold text-navy-900 focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50"
      >
        Skip to the portal
      </a>
      <header>
        <div className="bg-navy-900 text-white">
          <div className="mx-auto flex max-w-screen-2xl flex-wrap items-baseline justify-between gap-2 px-4 py-4">
            <p className="text-xl font-extrabold tracking-wide">
              IONITY <span className="font-semibold text-cyan-brand">Board Portal</span>
            </p>
            <p className="text-sm text-slate-300">ESP32-MCP · flash · provision · console</p>
          </div>
        </div>
        {sim ? (
          <div className="border-b-4 border-orange-brand bg-orange-50 text-orange-950 dark:bg-orange-950/40 dark:text-orange-100" data-testid="sim-banner">
            <p className="mx-auto max-w-screen-2xl px-4 py-3 text-sm">
              {/* navy on orange: 6.7:1 (white on #ff6b35 would be 2.8:1) */}
              <strong className="mr-2 rounded bg-orange-brand px-2 py-0.5 text-xs font-black tracking-widest text-navy-950">SIMULATED BOARD</strong>
              {sim.label}. Nothing here touches real hardware. Networks it can see: <em>IONITY-LAB-IOT</em> (demo password lab-pass-123),{' '}
              <em>Afrihost Fibre DTM</em>, <em>Guest</em> (open).
            </p>
          </div>
        ) : null}
      </header>

      <main id="main" className="mx-auto flex max-w-screen-2xl flex-col gap-4 px-4 py-6">
        <h1 className="sr-only">Ionity Board Portal</h1>
        {!sim && !hasSerial ? (
          <div className="card border-l-4 border-l-bad" data-testid="unsupported">
            <h2 className="font-bold">This browser cannot open serial ports</h2>
            <p className="text-sm">
              The portal talks to boards over Web Serial. Open it in Chrome, Edge or Opera on a desktop computer.
            </p>
          </div>
        ) : null}
        {!sim && hasSerial && !secure ? (
          <div className="card border-l-4 border-l-bad">
            <h2 className="font-bold">Open the portal over https:// or from localhost</h2>
            <p className="text-sm">Browsers only allow serial ports on secure pages.</p>
          </div>
        ) : null}
        <DeviceConsole {...(sim ? { serial: sim.serial } : {})} simulated={sim !== null} />
      </main>

      <footer className="border-t border-slate-200 dark:border-navy-800">
        <div className="mx-auto flex max-w-screen-2xl flex-col gap-1 px-4 py-4 text-xs text-slate-600 dark:text-slate-400">
          <p>
            Policy 986 AED · License AED 900 · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved - TM2 · Building Tomorrow, Today.
          </p>
          <p>
            <a className="underline" href="https://www.ionity.today" rel="noopener">
              www.ionity.today
            </a>{' '}
            ·{' '}
            <a className="underline" href="https://www.ionity.co.za" rel="noopener">
              www.ionity.co.za
            </a>
          </p>
        </div>
      </footer>
    </AnnouncerProvider>
  );
}
