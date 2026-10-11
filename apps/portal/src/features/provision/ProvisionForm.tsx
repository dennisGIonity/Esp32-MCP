/*
 * AEDI - IONITY GLOBAL | @ionity/portal | ProvisionForm.tsx | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * What gets written into the board's NVS: Wi-Fi, MCP host URL, device UUID,
 * and (under "Advanced") fleet metadata, broker login, tokens and a CA cert.
 *
 * Validation (forms guidance): checked when a field loses focus, cleared as the
 * person types, and checked in full on submit - the submit button is never
 * disabled to block validation, only while a write is running. On errors the
 * first invalid field receives focus; messages are tied to their fields with
 * aria-invalid / aria-errormessage / aria-describedby.
 *
 * SECURITY: secret inputs are type=password with autocomplete off; their values
 * live only in React state for this tab and are never logged or stored.
 */
import {
  planSet,
  uuidv7,
  validateForm,
  type BoardInfo,
  type FieldErrors,
  type FormField,
  type Network,
  type ProvClient,
  type ProvisionForm as Form,
  type SetPlan,
  type ValidForm,
} from '@ionity/prov';
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { normalizeApiBase } from '../../lib/host/verify.ts';
import { useAnnounce } from '../../ui/announcer.tsx';
import { controlProps, Field, useFieldIds } from './Field.tsx';

/** DOM order - the first invalid one in this order gets focus. */
const FIELD_ORDER: readonly FormField[] = ['ssid', 'pass', 'hostUrl', 'deviceUuid', 'role', 'site', 'group', 'label', 'mqttUser', 'mqttPass', 'mcpToken', 'fleetToken', 'caPem'];
const ADVANCED: ReadonlySet<FormField> = new Set<FormField>(['role', 'site', 'group', 'label', 'mqttUser', 'mqttPass', 'mcpToken', 'fleetToken', 'caPem']);

export type SetField = <K extends FormField>(field: K, value: Form[K]) => void;

interface Props {
  readonly form: Form;
  readonly setField: SetField;
  readonly board: BoardInfo | null;
  readonly client: ProvClient | null;
  readonly connected: boolean;
  readonly busy: boolean;
  /** errors reported by the board during the last write */
  readonly externalErrors: FieldErrors;
  readonly hostApi: string;
  readonly onHostApiChange: (value: string) => void;
  readonly onSubmit: (valid: ValidForm, plan: SetPlan, warnings: readonly string[]) => void;
}

export function ProvisionForm(props: Props) {
  const { form, setField, board, client, connected, busy, externalErrors } = props;
  const announce = useAnnounce();
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<readonly string[]>([]);
  const [showPass, setShowPass] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [networks, setNetworks] = useState<readonly Network[]>([]);
  const [scanState, setScanState] = useState<{ busy: boolean; text: string }>({ busy: false, text: '' });
  const [hostApiDraft, setHostApiDraft] = useState(props.hostApi);
  const [hostApiError, setHostApiError] = useState<string | undefined>(undefined);
  const controls = useRef(new Map<FormField, HTMLElement>());
  const formErrorRef = useRef<HTMLParagraphElement>(null);

  // Board-reported errors replace the client-side ones for those fields.
  useEffect(() => {
    if (Object.keys(externalErrors).length === 0) return;
    setErrors((prev) => ({ ...prev, ...externalErrors }));
    focusFirst(externalErrors);
    // focusFirst only reads refs
  }, [externalErrors]);

  const reg = (field: FormField) => (el: HTMLElement | null) => {
    if (el) controls.current.set(field, el);
    else controls.current.delete(field);
  };

  function focusFirst(errs: FieldErrors): void {
    const first = FIELD_ORDER.find((f) => errs[f]);
    if (!first) return;
    if (ADVANCED.has(first)) setAdvancedOpen(true);
    // after <details> has opened
    requestAnimationFrame(() => controls.current.get(first)?.focus());
  }

  function update<K extends FormField>(field: K, value: Form[K]): void {
    setField(field, value);
    if (errors[field]) setErrors((prev) => without(prev, field));
    if (formError) setFormError(null);
  }

  /**
   * Validate on blur: show (or clear) only this field's message. A field the person
   * merely tabbed through (still empty, no message yet) is left alone until submit.
   *
   * Not when focus is moving to a button or <summary>: a message appearing between
   * mousedown and mouseup shifts the layout, and the click lands on something else
   * (a swallowed "Write to board"). Submit validates everything anyway.
   */
  function blur(field: FormField, next: EventTarget | null): void {
    if (next instanceof HTMLButtonElement || (next instanceof HTMLElement && next.tagName === 'SUMMARY')) return;
    if (form[field] === '' && !errors[field]) return;
    const r = validateForm(form);
    const msg = r.ok ? undefined : r.errors[field];
    setErrors((prev) => (msg ? { ...prev, [field]: msg } : without(prev, field)));
  }

  function submit(e: FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    if (busy) return;
    setFormError(null);
    const r = validateForm(form);
    if (!r.ok) {
      setErrors(r.errors);
      setWarnings(r.warnings);
      const n = Object.keys(r.errors).length;
      announce(`${n} ${n === 1 ? 'field needs' : 'fields need'} attention.`, { assertive: true });
      focusFirst(r.errors);
      return;
    }
    if (!connected || !board) {
      const msg = !connected ? 'Connect a board first (Connect board, at the top).' : 'Wait until the board is identified, or press Identify.';
      setFormError(msg);
      announce(msg, { assertive: true });
      requestAnimationFrame(() => formErrorRef.current?.focus());
      return;
    }
    const plan = planSet(r.value, board);
    const all = [...r.warnings, ...plan.warnings];
    if (form.keepPass && form.pass === '' && board.ssid && form.ssid !== board.ssid) {
      all.push(`The password kept on the board belongs to "${board.ssid}", not "${form.ssid}".`);
    }
    setWarnings(all);
    if (Object.keys(plan.errors).length > 0 || plan.formError) {
      setErrors(plan.errors);
      if (plan.formError) setFormError(plan.formError);
      announce(plan.formError ?? 'This board cannot store some of these settings.', { assertive: true });
      if (Object.keys(plan.errors).length > 0) focusFirst(plan.errors);
      else requestAnimationFrame(() => formErrorRef.current?.focus());
      return;
    }
    setErrors({});
    props.onSubmit(r.value, plan, all);
  }

  async function scan(): Promise<void> {
    if (!client) return;
    setScanState({ busy: true, text: 'Scanning (about 5 seconds)…' });
    try {
      const found = await client.scan();
      setNetworks(found);
      const text = found.length ? `Found ${found.length} network${found.length === 1 ? '' : 's'}. Pick one in the Wi-Fi name field.` : 'No 2.4 GHz networks found.';
      setScanState({ busy: false, text });
      announce(text);
    } catch (e) {
      const text = `Scan failed: ${(e as Error).message}`;
      setScanState({ busy: false, text });
      announce(text, { assertive: true });
    }
  }

  function commitHostApi(): void {
    const r = normalizeApiBase(hostApiDraft);
    if (!r.ok) {
      setHostApiError(`Fleet host API: ${r.error}.`);
      return;
    }
    setHostApiError(undefined);
    setHostApiDraft(r.value);
    props.onHostApiChange(r.value);
  }

  const v1 = board?.prov === 1;
  const ids = {
    ssid: useFieldIds(),
    pass: useFieldIds(),
    hostUrl: useFieldIds(),
    deviceUuid: useFieldIds(),
    role: useFieldIds(),
    site: useFieldIds(),
    group: useFieldIds(),
    label: useFieldIds(),
    mqttUser: useFieldIds(),
    mqttPass: useFieldIds(),
    mcpToken: useFieldIds(),
    fleetToken: useFieldIds(),
    caPem: useFieldIds(),
    hostApi: useFieldIds(),
  };
  const err = (f: FormField) => errors[f];
  const text = (f: Exclude<FormField, 'openNetwork' | 'keepPass' | 'role'>, hint: boolean, extra: Record<string, unknown> = {}) => ({
    ...controlProps(ids[f], err(f), hint),
    ref: reg(f),
    value: form[f],
    onChange: (e: { currentTarget: { value: string } }) => update(f, e.currentTarget.value),
    onBlur: (e: { relatedTarget: EventTarget | null }) => blur(f, e.relatedTarget),
    className: 'field-input',
    spellCheck: false,
    autoCapitalize: 'off',
    autoCorrect: 'off',
    ...extra,
  });

  return (
    <form className="card flex flex-col gap-5" noValidate onSubmit={submit} aria-labelledby="provision-title" data-testid="provision-form">
      <div>
        <h2 id="provision-title" className="text-lg font-bold text-navy-900 dark:text-slate-100">
          Board settings
        </h2>
        <p className="text-sm text-slate-600 dark:text-slate-300">
          Written to the board&rsquo;s settings storage (NVS) by its firmware - no recompiling. Empty optional fields keep what the board already has.
        </p>
      </div>

      <fieldset className="flex flex-col gap-4">
        <legend className="mb-2 text-base font-semibold">Wi-Fi</legend>
        <Field
          ids={ids.ssid}
          label="Wi-Fi name (SSID)"
          hint="2.4 GHz networks only - ESP32 boards cannot see 5 GHz."
          error={err('ssid')}
          addon={
            <button type="button" className="btn-secondary" onClick={() => void scan()} disabled={!connected || !board || busy || scanState.busy} aria-describedby="scan-status">
              {scanState.busy ? 'Scanning…' : 'Scan'}
            </button>
          }
        >
          <input {...text('ssid', true, { list: 'ssid-options', autoComplete: 'off', maxLength: 64 })} />
          <datalist id="ssid-options">
            {networks.map((n) => (
              <option key={n.ssid} value={n.ssid}>{`${n.rssi} dBm${n.open ? ', open' : ''}`}</option>
            ))}
          </datalist>
        </Field>
        <p id="scan-status" className="-mt-2 text-sm text-slate-600 empty:hidden dark:text-slate-300">
          {scanState.text}
        </p>

        <Field
          ids={ids.pass}
          label="Wi-Fi password"
          hint="8-63 characters. Sent to the board over USB only; never shown in the console or saved by this page."
          error={err('pass')}
          addon={
            <button type="button" className="btn-secondary" aria-pressed={showPass} aria-controls={ids.pass.id} onClick={() => setShowPass((s) => !s)}>
              {showPass ? 'Hide' : 'Show'}
            </button>
          }
        >
          <input
            {...text('pass', true, {
              type: showPass ? 'text' : 'password',
              autoComplete: 'off',
              disabled: form.openNetwork || (form.keepPass && form.pass === ''),
              maxLength: 64,
            })}
          />
        </Field>
        <div className="flex flex-col gap-2">
          <Check checked={form.openNetwork} onChange={(c) => (update('openNetwork', c), c && update('pass', ''), c && update('keepPass', false))}>
            Open network (no password)
          </Check>
          {board?.hasPass ? (
            <Check checked={form.keepPass} onChange={(c) => (update('keepPass', c), c && update('pass', ''), c && update('openNetwork', false))}>
              Keep the password already stored on the board
            </Check>
          ) : null}
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-4">
        <legend className="mb-2 text-base font-semibold">MCP host and identity</legend>
        <Field
          ids={ids.hostUrl}
          label="MCP host URL"
          optional
          hint={
            <>
              Where the board connects, e.g. <code>mqtt://192.168.0.3:1883</code> or <code>mqtts://fleet.example.lan</code>. Empty: the board looks for <code>ionity-fleet.local</code>.
              {v1 ? ' This board runs firmware 2.x, which speaks mqtt:// and http:// only.' : ''}
            </>
          }
          error={err('hostUrl')}
        >
          <input {...text('hostUrl', true, { inputMode: 'url', autoComplete: 'off', placeholder: 'mqtt://192.168.0.3:1883', maxLength: 200 })} />
        </Field>
        <Field
          ids={ids.deviceUuid}
          label="Device UUID"
          optional
          hint="The asset's permanent ID. When you replace a board, give the new one the old UUID so its history continues."
          error={err('deviceUuid')}
          addon={
            <button type="button" className="btn-secondary" onClick={() => update('deviceUuid', uuidv7())} disabled={busy}>
              Generate
            </button>
          }
        >
          <input {...text('deviceUuid', true, { autoComplete: 'off', className: 'field-input font-mono', placeholder: '0192f1c4-7d2a-7b3e-9a10-4c5d6e7f8a9b', maxLength: 40 })} />
        </Field>
      </fieldset>

      <details
        open={advancedOpen}
        onToggle={(e) => setAdvancedOpen(e.currentTarget.open)}
        className="rounded-lg border border-slate-200 p-3 dark:border-navy-800"
      >
        <summary className="cursor-pointer font-semibold">Advanced: fleet, security, verification</summary>
        <div className="mt-4 flex flex-col gap-4">
          <Field ids={ids.role} label="Role" hint="Node: connects to the MCP host. Standalone: serves its own LAN MCP endpoint only." error={err('role')}>
            <select
              {...controlProps(ids.role, err('role'), true)}
              ref={reg('role')}
              className="field-input"
              value={form.role}
              onChange={(e) => update('role', e.currentTarget.value === 'standalone' ? 'standalone' : 'node')}
            >
              <option value="node">Node (fleet)</option>
              <option value="standalone">Standalone</option>
            </select>
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field ids={ids.site} label="Site" optional hint="Lowercase letters, digits and -." error={err('site')}>
              <input {...text('site', true, { autoComplete: 'off', maxLength: 32 })} />
            </Field>
            <Field ids={ids.group} label="Group" optional hint="Lowercase letters, digits and -." error={err('group')}>
              <input {...text('group', true, { autoComplete: 'off', maxLength: 32 })} />
            </Field>
          </div>
          <Field ids={ids.label} label="Label" optional hint="Shown in dashboards, up to 48 bytes." error={err('label')}>
            <input {...text('label', true, { autoComplete: 'off' })} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field ids={ids.mqttUser} label="MQTT user" optional hint="If the broker requires a login." error={err('mqttUser')}>
              <input {...text('mqttUser', true, { autoComplete: 'off' })} />
            </Field>
            <Field ids={ids.mqttPass} label="MQTT password" optional hint="Never shown again after writing." error={err('mqttPass')}>
              <input {...text('mqttPass', true, { type: 'password', autoComplete: 'off' })} />
            </Field>
          </div>
          <Field ids={ids.mcpToken} label="MCP token" optional hint="Bearer token the board's own MCP endpoint will require (16-128 characters)." error={err('mcpToken')}>
            <input {...text('mcpToken', true, { type: 'password', autoComplete: 'off' })} />
          </Field>
          <Field ids={ids.fleetToken} label="Fleet token" optional hint="Fleet enrolment token (16-128 characters)." error={err('fleetToken')}>
            <input {...text('fleetToken', true, { type: 'password', autoComplete: 'off' })} />
          </Field>
          <Field
            ids={ids.caPem}
            label="CA certificate (PEM)"
            optional
            hint="Your private CA, for mqtts:// or wss:// hosts the built-in bundle does not cover. Firmware 3 only."
            error={err('caPem')}
          >
            <textarea {...text('caPem', true, { rows: 4, className: 'field-input font-mono text-xs' })} />
          </Field>
          <Field
            ids={ids.hostApi}
            label="Fleet host API (for the last check)"
            optional
            hint="Web address of the fleet host, e.g. http://192.168.0.3:8099. Remembered in this browser."
            error={hostApiError}
          >
            <input
              {...controlProps(ids.hostApi, hostApiError, true)}
              className="field-input"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              value={hostApiDraft}
              onChange={(e) => (setHostApiDraft(e.currentTarget.value), setHostApiError(undefined))}
              onBlur={commitHostApi}
            />
          </Field>
        </div>
      </details>

      {warnings.length > 0 ? (
        <div className="rounded-md bg-amber-50 p-3 text-sm text-amber-950 dark:bg-amber-950/40 dark:text-amber-100" data-testid="form-warnings">
          <p className="font-semibold">Before you write:</p>
          <ul className="ml-5 list-disc">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <p ref={formErrorRef} tabIndex={-1} className="text-sm font-semibold text-bad empty:hidden dark:text-red-300" data-testid="form-error">
        {formError ?? ''}
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? 'Writing…' : 'Write to board'}
        </button>
        <span className="text-sm text-slate-600 dark:text-slate-300">Then the board joins the Wi-Fi, reaches the host and restarts.</span>
      </div>
    </form>
  );
}

function without(errs: FieldErrors, field: FormField): FieldErrors {
  const next = { ...errs };
  delete next[field];
  return next;
}

function Check({ checked, onChange, children }: { readonly checked: boolean; readonly onChange: (checked: boolean) => void; readonly children: ReactNode }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm">
      <input type="checkbox" className="size-5 accent-navy-900 dark:accent-cyan-brand" checked={checked} onChange={(e) => onChange(e.currentTarget.checked)} />
      <span>{children}</span>
    </label>
  );
}
