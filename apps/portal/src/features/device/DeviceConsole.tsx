/*
 * AEDI - IONITY GLOBAL | @ionity/portal | DeviceConsole.tsx | Policy 986 AED
 * Doc ID: DOC-2026-10-ESP32MCP-PORTAL-P2 | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * THE CORE COMPONENT (Phase 2): connect to an ESP32 over Web Serial, write its
 * NVS provisioning data (Wi-Fi + MCP host + device UUID, through the firmware's
 * ionity-prov protocol) and stream its serial output into xterm.js.
 *
 *   useDevice        SerialSession (port owner) + ProvClient + log
 *   ProvisionForm    validated settings -> planSet() for THIS board's firmware
 *   usePairingFlow   write -> join Wi-Fi -> reach host -> reboot -> online -> listed
 *   TerminalPanel    xterm.js, secrets scrubbed, Save log
 *   FlashPanel       esptool-js through session.lease()
 *
 * Data never leaves the browser except to the board over USB and (optionally)
 * a GET of the board's hwid to the fleet host. Secrets stay in React state only.
 */
import { EMPTY_FORM, type FieldErrors, type FormField, type ProvisionForm as Form, type SetPlan, type ValidForm } from '@ionity/prov';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { defaultHostApi } from '../../lib/host/verify.ts';
import { prefs } from '../../lib/store.ts';
import { useAnnounce } from '../../ui/announcer.tsx';
import { TerminalPanel } from '../console/TerminalPanel.tsx';
import { FlashPanel } from '../flash/FlashPanel.tsx';
import { PairingChecklist } from '../provision/PairingChecklist.tsx';
import { ProvisionForm, type SetField } from '../provision/ProvisionForm.tsx';
import { usePairingFlow } from '../provision/usePairingFlow.ts';
import { BoardCard } from './BoardCard.tsx';
import { ConnectionBar } from './ConnectionBar.tsx';
import { useDevice } from './useDevice.ts';

interface Props {
  /** an injected Serial (the simulator); default navigator.serial */
  readonly serial?: Serial | null;
  readonly simulated?: boolean;
}

const NO_ERRORS: FieldErrors = {};

export function DeviceConsole({ serial, simulated = false }: Props) {
  const device = useDevice(serial);
  const flow = usePairingFlow(device);
  const announce = useAnnounce();
  const [flashing, setFlashing] = useState(false);
  const busy = flow.state.running || flashing;
  const { board, snapshot, identify } = device;

  // ---- form state (lifted: the console needs the secrets to mask them) -------------
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const touched = useRef(new Set<FormField>());
  // The simulated board is unknown to any real fleet host: no default host check there.
  const [hostApi, setHostApi] = useState(() => prefs.get('hostApi') ?? (simulated ? '' : defaultHostApi()));

  const setField = useCallback<SetField>((field, value) => {
    touched.current.add(field);
    setForm((f) => ({ ...f, [field]: value }));
  }, []);

  // Prefill from a newly identified board; never overwrite what the person typed.
  const prefilledFor = useRef<string | null>(null);
  useEffect(() => {
    if (!board || prefilledFor.current === board.hwid) return;
    prefilledFor.current = board.hwid;
    setForm((f) => {
      const next: Form = { ...f };
      const fill = <K extends FormField>(k: K, v: Form[K]) => {
        if (!touched.current.has(k)) next[k] = v;
      };
      fill('ssid', board.ssid ?? '');
      fill('keepPass', board.hasPass);
      fill('hostUrl', board.hostUrl);
      fill('deviceUuid', board.deviceUuid ?? '');
      fill('role', board.role === 'standalone' ? 'standalone' : 'node');
      fill('site', board.site ?? '');
      fill('group', board.group ?? '');
      fill('label', board.label ?? '');
      return next;
    });
  }, [board]);

  // A different board (or none): forget which fields were typed for the old one.
  useEffect(() => {
    if (snapshot.status === 'idle') {
      prefilledFor.current = null;
      touched.current.clear();
    }
  }, [snapshot.status]);

  const secrets = useMemo(() => [form.pass, form.mqttPass, form.mcpToken, form.fleetToken], [form.pass, form.mqttPass, form.mcpToken, form.fleetToken]);
  useEffect(() => device.setSecrets(secrets), [device.setSecrets, secrets]);

  // After re-enumeration (native USB reset, firmware install) ask the board who it is.
  const seenReconnects = useRef(0);
  useEffect(() => {
    if (snapshot.status !== 'open' || snapshot.reconnects === seenReconnects.current) return;
    seenReconnects.current = snapshot.reconnects;
    if (!flow.state.running) void identify();
  }, [snapshot.status, snapshot.reconnects, flow.state.running, identify]);

  const onHostApiChange = useCallback((v: string) => {
    setHostApi(v);
    prefs.set('hostApi', v);
  }, []);

  const onSubmit = useCallback(
    (valid: ValidForm, plan: SetPlan, warnings: readonly string[]) => {
      for (const w of warnings) device.note(w, 'warn');
      void flow.start({ form: valid, plan, hostApi });
    },
    [device, flow, hostApi],
  );

  const factoryReset = useCallback(async () => {
    if (!device.client) return;
    try {
      await device.client.factoryReset();
      device.note('Factory reset: the board erased its settings and restarts.', 'warn');
      announce('Factory reset done. The board restarts with empty settings.');
      prefilledFor.current = null;
      touched.current.clear();
      setForm(EMPTY_FORM);
      flow.reset();
    } catch (e) {
      const msg = `Factory reset failed: ${(e as Error).message}`;
      device.note(msg, 'error');
      announce(msg, { assertive: true });
    }
  }, [device, announce, flow]);

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]" data-testid="device-console">
      <div className="flex min-w-0 flex-col gap-4">
        <ConnectionBar device={device} busy={busy} />
        {board ? <BoardCard board={board} busy={busy || snapshot.status !== 'open'} onFactoryReset={factoryReset} /> : null}
        <ProvisionForm
          form={form}
          setField={setField}
          board={board}
          client={device.client}
          connected={snapshot.status === 'open'}
          busy={busy}
          externalErrors={flow.state.fieldErrors ?? NO_ERRORS}
          hostApi={hostApi}
          onHostApiChange={onHostApiChange}
          onSubmit={onSubmit}
        />
        <PairingChecklist flow={flow.state} onStop={flow.stop} />
        <FlashPanel device={device} busy={busy} onBusyChange={setFlashing} simulated={simulated} />
      </div>
      <div className="min-w-0 lg:sticky lg:top-4">
        <TerminalPanel device={device} secrets={secrets} />
      </div>
    </div>
  );
}
