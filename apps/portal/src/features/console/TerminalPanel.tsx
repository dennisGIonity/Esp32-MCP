/*
 * AEDI - IONITY GLOBAL | @ionity/portal | TerminalPanel.tsx | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The serial console: live board output in xterm.js (colours kept), portal
 * notes interleaved, a labelled input to send a line to the board, and
 * "Save log" (scrubbed text of the session).
 *
 * The output area is deliberately NOT an ARIA live region (a booting ESP32
 * prints hundreds of lines). Screen-reader users can switch on xterm's
 * screen-reader mode; pairing progress is announced separately.
 */
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { prefs } from '../../lib/store.ts';
import type { Device } from '../device/useDevice.ts';
import { controlProps, useFieldIds } from '../provision/Field.tsx';
import { useTerminal } from './useTerminal.ts';

interface Props {
  readonly device: Device;
  /** values typed into the form that must never be shown */
  readonly secrets: readonly string[];
}

export function TerminalPanel({ device, secrets }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const term = useTerminal(hostRef);
  const { session, onNote, board, snapshot } = device;
  const [srMode, setSrMode] = useState(() => prefs.get('screenReader') === '1');
  const [line, setLine] = useState('');
  const [sendError, setSendError] = useState<string | undefined>(undefined);
  const ids = useFieldIds();

  useEffect(() => session.onBytes((chunk) => term.writeBytes(chunk)), [session, term]);
  useEffect(() => onNote((n) => term.note(n.text, n.tone)), [onNote, term]);
  useEffect(() => term.setSecrets(secrets), [term, secrets]);
  useEffect(() => {
    term.setScreenReaderMode(srMode);
    prefs.set('screenReader', srMode ? '1' : '');
  }, [term, srMode]);

  function clear(): void {
    term.clear();
    device.clearLog();
  }

  function saveLog(): void {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const header = [
      '# Ionity ESP32-MCP board log - Policy 986 AED - (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd',
      `# board: ${board?.hwid ?? 'unknown'}  fw: ${board?.fw ?? '?'}  chip: ${board?.chip ?? '?'}  saved: ${new Date().toISOString()}`,
      '# Values typed into the portal as passwords or tokens are masked as •••. Lines starting with "#" are portal notes.',
      '',
    ].join('\n');
    const url = URL.createObjectURL(new Blob([header, device.logText()], { type: 'text/plain;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `ionity-${board?.hwid ?? 'board'}-${stamp}.log`;
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  async function send(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    if (snapshot.status !== 'open') {
      setSendError('Connect a board first.');
      return;
    }
    if (line === '') return;
    try {
      await session.write(`${line}\n`);
      device.note(`sent: ${line}`, 'tx');
      setLine('');
      setSendError(undefined);
    } catch (err) {
      setSendError((err as Error).message);
    }
  }

  return (
    <section className="card flex min-w-0 flex-col gap-3" aria-labelledby="console-title" data-testid="console">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="console-title" className="text-lg font-bold text-navy-900 dark:text-slate-100">
          Serial console
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-secondary" onClick={clear}>
            Clear
          </button>
          <button type="button" className="btn-secondary" onClick={saveLog}>
            Save log
          </button>
        </div>
      </div>
      <div
        ref={hostRef}
        role="group"
        aria-label="Board output"
        className="terminal-host overflow-hidden rounded-lg border border-navy-800 bg-navy-950 p-1"
        data-testid="terminal"
      />
      <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm">
        <input type="checkbox" className="size-5 accent-navy-900 dark:accent-cyan-brand" checked={srMode} onChange={(e) => setSrMode(e.currentTarget.checked)} />
        <span>Screen-reader mode for the console (reads output line by line)</span>
      </label>
      <form className="flex flex-col gap-1" onSubmit={(e) => void send(e)} noValidate>
        <label htmlFor={ids.id} className="text-sm font-semibold">
          Send a line to the board
        </label>
        <p id={ids.hintId} className="text-sm text-slate-600 dark:text-slate-300">
          Sent with a newline. For firmware console commands; settings belong in the form.
        </p>
        <div className="flex gap-2">
          <input
            {...controlProps(ids, sendError, true)}
            className="field-input font-mono"
            value={line}
            onChange={(e) => (setLine(e.currentTarget.value), setSendError(undefined))}
            autoComplete="off"
            spellCheck={false}
            autoCapitalize="off"
          />
          <button type="submit" className="btn-secondary">
            Send
          </button>
        </div>
        <p id={ids.errorId} className="text-sm font-medium text-bad empty:hidden dark:text-red-300">
          {sendError ?? ''}
        </p>
      </form>
    </section>
  );
}
