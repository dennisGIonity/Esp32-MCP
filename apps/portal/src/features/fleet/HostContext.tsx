/*
 * AEDI - IONITY GLOBAL | @ionity/portal | HostContext.tsx | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
 *
 * One connection to the fleet host for the whole portal: the REST client, the
 * /ws/fleet device list and the /ws/events DNS + flag feed. The sockets open
 * lazily (the first time a fleet view is shown), so the USB-only workflow never
 * talks to a host it does not need.
 *
 * The admin token is React state only. It is never stored and never logged.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from 'react';
import { FleetApi, wsUrl, type FleetDevice } from '../../lib/api/client.ts';
import { EMPTY_LIVE, LiveSocket, liveReducer, parseFleetTick, type LiveState, type SocketState } from '../../lib/api/live.ts';
import { defaultHostApi, mixedContentBlocked } from '../../lib/host/verify.ts';
import { prefs } from '../../lib/store.ts';

export interface HostValue {
  readonly base: string;
  readonly setBase: (v: string) => void;
  readonly adminToken: string;
  readonly setAdminToken: (v: string) => void;
  readonly api: FleetApi;
  readonly configured: boolean;
  readonly blocked: string | null;
  readonly devices: readonly FleetDevice[];
  readonly devicesState: SocketState;
  readonly live: LiveState;
  readonly liveState: SocketState;
  readonly liveDetail: string;
  /** call when a fleet view is shown: opens the sockets once */
  readonly activate: () => void;
  readonly refreshDevices: () => Promise<void>;
}

const HostContext = createContext<HostValue | null>(null);

export function useHost(): HostValue {
  const v = useContext(HostContext);
  if (!v) throw new Error('useHost outside <HostProvider>');
  return v;
}

/** The host for a given USB board, if the fleet lists it. */
export function useFleetDevice(hwid: string | undefined): FleetDevice | null {
  const ctx = useContext(HostContext);
  if (!ctx || !hwid) return null;
  return ctx.devices.find((d) => d.device_id === hwid) ?? null;
}

interface Props {
  readonly children: ReactNode;
  readonly simulated: boolean;
  readonly WebSocketImpl?: typeof WebSocket;
}

export function HostProvider({ children, simulated, WebSocketImpl }: Props) {
  const [base, setBaseState] = useState(() => prefs.get('hostApi') ?? (simulated ? '' : defaultHostApi()));
  const [adminToken, setAdminToken] = useState('');
  const [active, setActive] = useState(false);
  const [devices, setDevices] = useState<readonly FleetDevice[]>([]);
  const [devicesState, setDevicesState] = useState<SocketState>('closed');
  const [live, dispatch] = useReducer((s: LiveState, m: unknown) => (m === RESET ? EMPTY_LIVE : liveReducer(s, m)), EMPTY_LIVE);
  const [liveState, setLiveState] = useState<SocketState>('closed');
  const [liveDetail, setLiveDetail] = useState('');

  const api = useMemo(() => new FleetApi(base, adminToken ? { adminToken } : {}), [base, adminToken]);
  const configured = base !== '';
  const blocked = configured && mixedContentBlocked(base) ? 'This page is https:// and the host is http:// - the browser blocks that. Open the portal from the host itself (/portal/).' : null;

  const setBase = useCallback((v: string) => {
    setBaseState(v);
    prefs.set('hostApi', v);
  }, []);

  const activate = useCallback(() => setActive(true), []);

  const refreshDevices = useCallback(async () => {
    if (!configured || blocked) return;
    try {
      setDevices(await api.devices());
    } catch {
      /* the socket keeps trying; the views show its state */
    }
  }, [api, configured, blocked]);

  // Sockets: (re)open when the host changes, only once a fleet view was shown.
  const sockets = useRef<LiveSocket[]>([]);
  useEffect(() => {
    if (!active || !configured || blocked) return;
    dispatch(RESET);
    setDevices([]);
    const opts = WebSocketImpl ? { WebSocketImpl } : {};
    const fleet = new LiveSocket(wsUrl(base, '/ws/fleet'), {
      ...opts,
      onMessage: (m) => {
        const t = parseFleetTick(m);
        if (t) setDevices(t.devices);
      },
      onState: (s) => setDevicesState(s),
    });
    const events = new LiveSocket(wsUrl(base, '/ws/events'), {
      ...opts,
      onMessage: dispatch,
      onState: (s, d) => {
        setLiveState(s);
        setLiveDetail(d ?? '');
      },
    });
    sockets.current = [fleet, events];
    return () => {
      for (const s of sockets.current) s.close();
      sockets.current = [];
    };
  }, [active, configured, blocked, base, WebSocketImpl]);

  const value = useMemo<HostValue>(
    () => ({ base, setBase, adminToken, setAdminToken, api, configured, blocked, devices, devicesState, live, liveState, liveDetail, activate, refreshDevices }),
    [base, setBase, adminToken, api, configured, blocked, devices, devicesState, live, liveState, liveDetail, activate, refreshDevices],
  );
  return <HostContext.Provider value={value}>{children}</HostContext.Provider>;
}

const RESET = Symbol('reset');
