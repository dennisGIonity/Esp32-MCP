/*
 * AEDI - IONITY GLOBAL | @ionity/portal | announcer.tsx | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Screen-reader announcements: exactly one polite and one assertive live region
 * for the whole app, rendered (empty) from the first paint - a live region that
 * is inserted together with its text is often not announced.
 *
 * Polite: progress ("Board identified", "Settings written").
 * Assertive: failures that need attention now ("Wi-Fi password rejected").
 * The serial console itself is NOT a live region; it would flood the reader.
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';

type Announce = (message: string, opts?: { readonly assertive?: boolean }) => void;

const AnnounceContext = createContext<Announce>(() => undefined);

export function AnnouncerProvider({ children }: { readonly children: ReactNode }) {
  const [polite, setPolite] = useState('');
  const [assertive, setAssertive] = useState('');
  const timers = useRef<{ polite?: number; assertive?: number }>({});

  const announce = useCallback<Announce>((message, opts) => {
    const key = opts?.assertive ? 'assertive' : 'polite';
    const set = opts?.assertive ? setAssertive : setPolite;
    // Clear first, then set: the same text twice in a row is announced again.
    set('');
    window.clearTimeout(timers.current[key]);
    timers.current[key] = window.setTimeout(() => set(message), 60);
  }, []);

  const value = useMemo(() => announce, [announce]);
  return (
    <AnnounceContext.Provider value={value}>
      {children}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true" data-testid="live-polite">
        {polite}
      </div>
      <div className="sr-only" role="alert" aria-live="assertive" aria-atomic="true" data-testid="live-assertive">
        {assertive}
      </div>
    </AnnounceContext.Provider>
  );
}

export function useAnnounce(): Announce {
  return useContext(AnnounceContext);
}
