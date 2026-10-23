/*
 * AEDI - IONITY GLOBAL | @ionity/portal | App.tsx | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
 * www.ionity.today | www.ionity.world | Ref: www.ionity.co.za
 */
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { DeviceConsole } from '../features/device/DeviceConsole.tsx';
import { FleetPanel } from '../features/fleet/FleetPanel.tsx';
import { HostProvider, useHost } from '../features/fleet/HostContext.tsx';
import { HostSettings } from '../features/fleet/HostSettings.tsx';
import { FlaggedBanner } from '../features/network/FlaggedBanner.tsx';
import { NetworkActivity } from '../features/network/NetworkActivity.tsx';
import { WatchlistManager } from '../features/watchlist/WatchlistManager.tsx';
import type { Simulation } from '../sim/simulator.ts';
import { AnnouncerProvider } from '../ui/announcer.tsx';

const TABS = [
  { id: 'board', label: 'Board (USB)' },
  { id: 'fleet', label: 'Fleet' },
  { id: 'network', label: 'Network activity' },
  { id: 'watchlist', label: 'Watchlist' },
] as const;
type TabId = (typeof TABS)[number]['id'];

function tabFromHash(): TabId {
  const h = typeof location === 'undefined' ? '' : location.hash.replace(/^#/, '');
  return (TABS.find((t) => t.id === h)?.id ?? 'board') as TabId;
}

export function App({ sim }: { readonly sim: Simulation | null }) {
  return (
    <AnnouncerProvider>
      <HostProvider simulated={sim !== null}>
        <Shell sim={sim} />
      </HostProvider>
    </AnnouncerProvider>
  );
}

function Tabs({ tab, onTab }: { readonly tab: TabId; readonly onTab: (t: TabId) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  function onKey(e: KeyboardEvent<HTMLDivElement>): void {
    const i = TABS.findIndex((t) => t.id === tab);
    const next = e.key === 'ArrowRight' ? (i + 1) % TABS.length : e.key === 'ArrowLeft' ? (i - 1 + TABS.length) % TABS.length : e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : -1;
    if (next < 0) return;
    e.preventDefault();
    onTab(TABS[next]!.id);
    refs.current[next]?.focus();
  }
  return (
    <div role="tablist" aria-label="Portal sections" className="flex flex-wrap gap-1 border-b border-slate-300 dark:border-navy-700" onKeyDown={onKey}>
      {TABS.map((t, i) => (
        <button
          key={t.id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          role="tab"
          type="button"
          id={`tab-${t.id}`}
          aria-selected={tab === t.id}
          aria-controls={`panel-${t.id}`}
          tabIndex={tab === t.id ? 0 : -1}
          onClick={() => onTab(t.id)}
          data-testid={`tab-${t.id}`}
          className={`-mb-px min-h-11 rounded-t-md border-b-4 px-4 py-2 text-sm font-semibold ${
            tab === t.id ? 'border-cyan-brand text-navy-900 dark:text-white' : 'border-transparent text-slate-600 hover:text-navy-900 dark:text-slate-300 dark:hover:text-white'
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function Shell({ sim }: { readonly sim: Simulation | null }) {
  const hasSerial = typeof navigator !== 'undefined' && 'serial' in navigator;
  const secure = typeof window !== 'undefined' && window.isSecureContext;
  const host = useHost();
  const [tab, setTab] = useState<TabId>(tabFromHash);
  const { activate } = host;

  useEffect(() => {
    const on = () => setTab(tabFromHash());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  // The host feeds open the first time a fleet view is shown - or straight away
  // when the portal is served by the host itself (/portal/), so the flagged
  // banner works on the Board tab too.
  useEffect(() => {
    if (tab !== 'board' || (!sim && /\/portal(\/|$)/.test(location.pathname))) activate();
  }, [tab, sim, activate]);

  const choose = (t: TabId) => {
    setTab(t);
    history.replaceState(null, '', t === 'board' ? location.pathname + location.search : `#${t}`);
  };

  return (
    <>
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
            <p className="text-sm text-slate-300">ESP32-MCP · flash · provision · fleet · network activity</p>
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
        {tab === 'network' ? <FlaggedBanner /> : <FlaggedBanner onShow={() => choose('network')} />}
      </header>

      <main id="main" className="mx-auto flex max-w-screen-2xl flex-col gap-4 px-4 py-6">
        <h1 className="sr-only">Ionity Board Portal</h1>
        <Tabs tab={tab} onTab={choose} />
        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="flex flex-col gap-4">
        {tab !== 'board' ? <HostSettings /> : null}
        {tab === 'fleet' ? <FleetPanel /> : null}
        {tab === 'network' ? <NetworkActivity /> : null}
        {tab === 'watchlist' ? <WatchlistManager /> : null}
        {tab === 'board' && !sim && !hasSerial ? (
          <div className="card border-l-4 border-l-bad" data-testid="unsupported">
            <h2 className="font-bold">This browser cannot open serial ports</h2>
            <p className="text-sm">
              The portal talks to boards over Web Serial. Open it in Chrome, Edge or Opera on a desktop computer.
            </p>
          </div>
        ) : null}
        {tab === 'board' && !sim && hasSerial && !secure ? (
          <div className="card border-l-4 border-l-bad">
            <h2 className="font-bold">Open the portal over https:// or from localhost</h2>
            <p className="text-sm">Browsers only allow serial ports on secure pages.</p>
          </div>
        ) : null}
        {/* kept mounted (hidden) on other tabs: the serial session and console survive a tab switch */}
        <div hidden={tab !== 'board'}>
          <DeviceConsole {...(sim ? { serial: sim.serial } : {})} simulated={sim !== null} />
        </div>
        </div>
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
    </>
  );
}
