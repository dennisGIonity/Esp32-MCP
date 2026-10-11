/*
 * AEDI - IONITY GLOBAL | @ionity/portal | useDevice.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * One board, one serial session, one provisioning client - wired for React.
 *
 *   SerialSession  (lib/serial)   owns the Web Serial port
 *   ProvClient     (@ionity/prov) ionity-prov/1+2 request/reply over its lines
 *   LineHistory                   bounded, scrubbed log for "Save log"
 *   notes                         portal annotations (TX lines, notices) for the console
 *
 * StrictMode: the session is created by a useState initializer (no side effects
 * in its constructor); listeners and the client are attached in effects and
 * removed in their cleanups, so a double mount leaves exactly one of each.
 */
import { ProvClient, type BoardInfo } from '@ionity/prov';
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { LineHistory } from '../../lib/serial/lines.ts';
import { SecretScrubber } from '../../lib/serial/scrub.ts';
import { SerialSession, type SessionSnapshot } from '../../lib/serial/session.ts';
import { useAnnounce } from '../../ui/announcer.tsx';
import type { NoteTone } from '../console/useTerminal.ts';

export interface ConsoleNote {
  readonly text: string;
  readonly tone: NoteTone;
}

export interface Device {
  readonly session: SerialSession;
  readonly client: ProvClient | null;
  readonly snapshot: SessionSnapshot;
  /** identity reported by the firmware; null until the handshake answers */
  readonly board: BoardInfo | null;
  readonly identifying: boolean;
  readonly identifyError: string | null;
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  resetBoard(): Promise<void>;
  identify(): Promise<BoardInfo | null>;
  note(text: string, tone: NoteTone): void;
  onNote(listener: (n: ConsoleNote) => void): () => void;
  /** scrubbed text of the session log, for "Save log" */
  logText(): string;
  clearLog(): void;
  /** values to mask in the saved log (the console has its own scrubber) */
  setSecrets(values: readonly string[]): void;
}

export function useDevice(serial?: Serial | null): Device {
  const announce = useAnnounce();
  const [session] = useState(() => new SerialSession(serial !== undefined ? { serial } : {}));
  const [aux] = useState(() => ({ history: new LineHistory(), scrubber: new SecretScrubber(), noteListeners: new Set<(n: ConsoleNote) => void>() }));
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
  const [client, setClient] = useState<ProvClient | null>(null);
  const [board, setBoard] = useState<BoardInfo | null>(null);
  const [identifying, setIdentifying] = useState(false);
  const [identifyError, setIdentifyError] = useState<string | null>(null);

  const note = useCallback(
    (text: string, tone: NoteTone) => {
      const safe = aux.scrubber.line(text);
      aux.history.push(`# ${safe}`);
      for (const l of aux.noteListeners) l({ text: safe, tone });
    },
    [aux],
  );

  // Global "connect" listener for re-enumerating native-USB boards.
  useEffect(() => {
    session.attach();
    return () => session.detachAll();
  }, [session]);

  // Line history + session notices.
  useEffect(() => {
    const offLine = session.onLine((line) => aux.history.push(aux.scrubber.line(line)));
    const offNotice = session.onNotice((n) => {
      note(n.text, n.tone);
      if (n.tone !== 'info') announce(n.text, { assertive: n.tone === 'error' });
    });
    return () => {
      offLine();
      offNotice();
    };
  }, [session, aux, note, announce]);

  // Provisioning client: one per session, disposed with it.
  useEffect(() => {
    const c = new ProvClient(session, (text, kind) => note(text, kind === 'tx' ? 'tx' : kind));
    const offBoard = c.onBoard((b) => setBoard(b));
    setClient(c);
    return () => {
      offBoard();
      c.dispose();
      setClient(null);
    };
  }, [session, note]);

  // A closed port means a different board may come next.
  useEffect(() => {
    if (snapshot.status === 'idle') {
      setBoard(null);
      setIdentifyError(null);
      client?.forgetBoard();
    }
  }, [snapshot.status, client]);

  const identify = useCallback(async (): Promise<BoardInfo | null> => {
    if (!client) return null;
    setIdentifying(true);
    setIdentifyError(null);
    try {
      const b = await client.handshake(15_000);
      setBoard(b);
      const fw = b.fw ? `firmware ${b.fw}` : 'unknown firmware';
      announce(`Board identified: ${b.chip ?? 'ESP32'}, ${fw}, provisioning protocol ${b.prov}.`);
      return b;
    } catch (e) {
      const msg = (e as Error).message;
      setIdentifyError(msg);
      note(`Board did not identify itself: ${msg}`, 'warn');
      announce(`The board did not answer. ${msg}`, { assertive: true });
      return null;
    } finally {
      setIdentifying(false);
    }
  }, [client, note, announce]);

  const connect = useCallback(async () => {
    try {
      await session.choosePort();
    } catch (e) {
      const err = e as DOMException;
      if (err.name === 'NotFoundError') return; // the picker was cancelled: not an error
      note(err.message, 'error');
      announce(err.message, { assertive: true });
      return;
    }
    const d = session.getSnapshot().port;
    note(`Connected: ${d?.bridge ?? 'serial port'} at 115200 baud.`, 'info');
    announce('Board connected. Identifying…');
    await identify();
  }, [session, identify, note, announce]);

  const disconnect = useCallback(async () => {
    try {
      await session.close();
      note('Disconnected.', 'info');
      announce('Board disconnected.');
    } catch (e) {
      note((e as Error).message, 'error');
    }
  }, [session, note, announce]);

  const resetBoard = useCallback(async () => {
    await session.resetToApp();
  }, [session]);

  // Stable identities: components subscribe in effects keyed on these.
  const onNote = useCallback(
    (listener: (n: ConsoleNote) => void) => {
      aux.noteListeners.add(listener);
      return () => {
        aux.noteListeners.delete(listener);
      };
    },
    [aux],
  );
  const logText = useCallback(() => aux.history.text(), [aux]);
  const clearLog = useCallback(() => aux.history.clear(), [aux]);
  const setSecrets = useCallback((values: readonly string[]) => aux.scrubber.set(values), [aux]);

  return useMemo<Device>(
    () => ({ session, client, snapshot, board, identifying, identifyError, connect, disconnect, resetBoard, identify, note, onNote, logText, clearLog, setSecrets }),
    [session, client, snapshot, board, identifying, identifyError, connect, disconnect, resetBoard, identify, note, onNote, logText, clearLog, setSecrets],
  );
}
