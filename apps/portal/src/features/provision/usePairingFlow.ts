/*
 * AEDI - IONITY GLOBAL | @ionity/portal | usePairingFlow.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The pairing sequence after "Write to board", as a checklist a person can follow:
 *
 *   connect   serial port open
 *   identify  firmware answered hello (prov/1 or prov/2)
 *   write     `set` accepted - NVS written by the FIRMWARE (validate-then-write on fw 3)
 *   wifi      `test`: joined the Wi-Fi with the new settings, without rebooting
 *   broker    `test`: reached the MCP host (skipped for standalone boards)
 *   reboot    restarted; native-USB boards re-enumerate and the session reattaches
 *   online    after boot: Wi-Fi up and host session up
 *   host      the fleet host lists the board (optional)
 *
 * A failed step stops the sequence with a sentence that says what to do next.
 * Stop (AbortController) ends the wait between and during steps; a request the
 * board is already executing cannot be recalled, and the message says so.
 */
import { ProvError, type BoardInfo, type FormField, type SetFieldName, type SetPlan, type ValidForm } from '@ionity/prov';
import { useCallback, useRef, useState } from 'react';
import { checkBoardOnHost } from '../../lib/host/verify.ts';
import { useAnnounce } from '../../ui/announcer.tsx';
import type { Device } from '../device/useDevice.ts';

export type StepId = 'connect' | 'identify' | 'write' | 'wifi' | 'broker' | 'reboot' | 'online' | 'host';
export type StepState = 'pending' | 'active' | 'done' | 'failed' | 'skipped';

export interface Step {
  readonly id: StepId;
  readonly label: string;
  readonly state: StepState;
  readonly detail?: string;
}

export interface FlowState {
  readonly running: boolean;
  readonly outcome: 'idle' | 'success' | 'failed' | 'stopped';
  readonly message: string;
  readonly steps: readonly Step[];
  /** a field the board itself rejected (prov/2 "field") */
  readonly fieldErrors: Partial<Record<FormField, string>>;
}

export interface PairingInput {
  readonly form: ValidForm;
  readonly plan: SetPlan;
  /** fleet host API base for the last step; "" skips it */
  readonly hostApi: string;
}

const LABELS: Record<StepId, string> = {
  connect: 'Serial connection open',
  identify: 'Board identified',
  write: 'Settings written to the board',
  wifi: 'Board joined the Wi-Fi',
  broker: 'Board reached the MCP host',
  reboot: 'Board restarted with the new settings',
  online: 'Board is online',
  host: 'Fleet host lists the board',
};

const ORDER: readonly StepId[] = ['connect', 'identify', 'write', 'wifi', 'broker', 'reboot', 'online', 'host'];

export const INITIAL_FLOW: FlowState = {
  running: false,
  outcome: 'idle',
  message: '',
  steps: ORDER.map((id) => ({ id, label: LABELS[id], state: 'pending' })),
  fieldErrors: {},
};

/** prov wire field -> form field, for errors the board reports. */
const FIELD_OF: Partial<Record<SetFieldName, FormField>> = {
  ssid: 'ssid',
  pass: 'pass',
  host_url: 'hostUrl',
  server: 'hostUrl',
  mqtt_port: 'hostUrl',
  http_port: 'hostUrl',
  device_uuid: 'deviceUuid',
  ca_pem: 'caPem',
  mqtt_user: 'mqttUser',
  mqtt_pass: 'mqttPass',
  mcp_token: 'mcpToken',
  fleet_token: 'fleetToken',
  role: 'role',
  site: 'site',
  group: 'group',
  label: 'label',
};

class Stopped extends Error {
  constructor() {
    super('stopped');
    this.name = 'Stopped';
  }
}

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal.aborted) return reject(new Stopped());
    const t = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => (clearTimeout(t), reject(new Stopped())), { once: true });
  });

/** Wait for `p`, but give up (Stopped) as soon as the signal aborts. */
function abortable<T>(p: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(new Stopped());
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new Stopped());
    signal.addEventListener('abort', onAbort, { once: true });
    p.then(
      (v) => (signal.removeEventListener('abort', onAbort), resolve(v)),
      (e: unknown) => (signal.removeEventListener('abort', onAbort), reject(e)),
    );
  });
}

export function usePairingFlow(device: Device) {
  const announce = useAnnounce();
  const [state, setState] = useState<FlowState>(INITIAL_FLOW);
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const reset = useCallback(() => {
    abortRef.current?.abort();
    setState(INITIAL_FLOW);
  }, []);

  const start = useCallback(
    async (input: PairingInput): Promise<void> => {
      const { client, session } = device;
      if (!client || abortRef.current) return;
      const ctl = new AbortController();
      abortRef.current = ctl;
      const signal = ctl.signal;
      const { form, plan, hostApi } = input;
      let current: StepId = 'connect';

      const mark = (id: StepId, s: StepState, detail?: string) => {
        if (s === 'active') current = id;
        setState((prev) => ({
          ...prev,
          steps: prev.steps.map((st) => (st.id === id ? { id, label: st.label, state: s, ...(detail !== undefined ? { detail } : {}) } : st)),
        }));
        if (s === 'done') announce(`${LABELS[id]}${detail ? `: ${detail}` : ''}.`);
        if (s === 'failed') announce(`${LABELS[id]} failed${detail ? `: ${detail}` : ''}.`, { assertive: true });
      };
      const finish = (outcome: FlowState['outcome'], message: string, fieldErrors: FlowState['fieldErrors'] = {}) => {
        setState((prev) => ({ ...prev, running: false, outcome, message, fieldErrors }));
        if (outcome === 'success') announce(message);
        else announce(message, { assertive: true });
        device.note(message, outcome === 'success' ? 'info' : 'warn');
      };

      setState({ ...INITIAL_FLOW, running: true });

      try {
        // 1 · connect --------------------------------------------------------------
        mark('connect', 'active');
        const snap = session.getSnapshot();
        if (snap.status !== 'open') throw new Error('the serial port is not open - connect the board first');
        mark('connect', 'done', snap.port?.bridge ?? 'serial');

        // 2 · identify -------------------------------------------------------------
        mark('identify', 'active');
        const board: BoardInfo | null = device.board ?? (await abortable(device.identify(), signal));
        if (!board) throw new Error('the board did not answer the hello request');
        mark('identify', 'done', `${board.chip ?? 'ESP32'} · fw ${board.fw ?? '?'} · prov/${board.prov}`);

        // 3 · write ----------------------------------------------------------------
        mark('write', 'active', `${Object.keys(plan.fields).length} settings, ${plan.requestBytes} bytes`);
        let changed: string[];
        try {
          changed = (await abortable(client.set(plan.fields), signal)).changed;
        } catch (e) {
          if (e instanceof ProvError && e.field) {
            const f = FIELD_OF[e.field as SetFieldName];
            mark('write', 'failed', `the board rejected "${e.field}": ${e.message}`);
            finish('failed', 'The board rejected a setting. Fix the marked field and write again.', f ? { [f]: `The board says: ${e.message}` } : {});
            return;
          }
          throw e;
        }
        // Field NAMES only - values may be secrets.
        mark('write', 'done', changed.length ? `changed: ${changed.join(', ')}` : 'nothing changed');

        // 4/5 · test (Wi-Fi + host) without rebooting --------------------------------
        mark('wifi', 'active', `joining "${form.ssid}"…`);
        const t = await abortable(client.test(20_000), signal);
        if (!t.wifi.ok) {
          mark('wifi', 'failed', t.wifi.error ?? 'could not join');
          finish(
            'failed',
            `The board could not join "${form.ssid}" (${t.wifi.error ?? 'no reason given'}). The settings are saved on the board; correct the Wi-Fi name or password and write again. ESP32 boards only see 2.4 GHz networks.`,
            /password|auth|rejected|4-way/i.test(t.wifi.error ?? '') ? { pass: 'The board could not log in with this password.' } : {},
          );
          return;
        }
        mark('wifi', 'done', [t.wifi.ip && `IP ${t.wifi.ip}`, t.wifi.rssi !== undefined && `${t.wifi.rssi} dBm`].filter(Boolean).join(', ') || 'joined');

        mark('broker', 'active');
        if (form.role === 'standalone') {
          mark('broker', 'skipped', 'standalone board - no MCP host');
        } else if (t.broker.ok === true) {
          mark('broker', 'done', t.broker.detail ?? 'connected');
        } else if (t.broker.ok === false) {
          mark('broker', 'failed', t.broker.detail ?? 'cannot reach the host');
          finish(
            'failed',
            'Wi-Fi works, but the board could not reach the MCP host. The settings are saved; check the host URL (and that the broker is running), then write again.',
            { hostUrl: 'The board could not reach this host.' },
          );
          return;
        } else {
          mark('broker', 'skipped', 'not reported by this firmware');
        }

        // 6 · reboot -----------------------------------------------------------------
        mark('reboot', 'active', 'restarting…');
        const t0 = Date.now();
        await abortable(client.reboot(), signal);
        // Native-USB boards drop off the bus here; the session reattaches and the
        // handshake keeps asking until the firmware answers.
        await sleep(400, signal);
        const after = await abortable(client.handshake(30_000), signal);
        mark('reboot', 'done', `back after ${((Date.now() - t0) / 1000).toFixed(1)} s`);

        // 7 · online -------------------------------------------------------------------
        mark('online', 'active', 'waiting for Wi-Fi and the host session…');
        const needHost = form.role === 'node';
        let b: BoardInfo = after;
        const until = Date.now() + 30_000;
        while (!(b.wifi === 'connected' && (!needHost || b.mqtt !== false))) {
          if (Date.now() > until) {
            mark('online', 'failed', b.wifi !== 'connected' ? `Wi-Fi ${b.wifiError ?? 'not up'}` : 'host session not up');
            finish('failed', 'The board restarted but did not come online within 30 s. Watch the console for the reason.');
            return;
          }
          await sleep(1500, signal);
          b = await abortable(client.get(), signal);
        }
        mark('online', 'done', [b.ip && `IP ${b.ip}`, b.mqtt === true && 'host session up', b.boardMcpUrl && `MCP ${b.boardMcpUrl}`].filter(Boolean).join(' · ') || 'online');

        // 8 · fleet host -------------------------------------------------------------
        mark('host', 'active');
        const hc = await checkBoardOnHost(hostApi, b.hwid, { signal, attempts: 6 });
        if (hc.status === 'listed') mark('host', 'done', hc.detail);
        else if (hc.status === 'skipped') mark('host', 'skipped', hc.reason);
        else mark('host', 'failed', hc.reason);

        finish(
          'success',
          hc.status === 'missing' || hc.status === 'error'
            ? `Board ${b.hwid} is online. The fleet host has not listed it yet (${hc.reason}).`
            : `Board ${b.hwid} is provisioned and online.`,
        );
      } catch (e) {
        if (e instanceof Stopped || signal.aborted) {
          mark(current, 'failed', 'stopped');
          finish('stopped', 'Stopped. A request the board was already executing may still complete; check the console.');
        } else {
          mark(current, 'failed', (e as Error).message);
          finish('failed', `${LABELS[current]} failed: ${(e as Error).message}`);
        }
      } finally {
        abortRef.current = null;
      }
    },
    [device, announce],
  );

  return { state, start, stop, reset };
}
