/*
 * AEDI - IONITY GLOBAL | @ionity/portal | useTerminal.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * xterm.js 6 bound to a React element.
 *
 *  - Lifecycle: the Terminal is created in an effect and disposed in its cleanup
 *    (StrictMode mounts twice in development; nothing leaks).
 *  - Throughput: serial bytes are decoded (streaming UTF-8), scrubbed, batched
 *    per animation frame and written in one call. xterm parses asynchronously;
 *    if more than HIGH_WATER characters are still unparsed (a board spamming logs
 *    at 921600 baud), new output is DROPPED and counted instead of growing the
 *    heap without bound. The saved log (line history) is unaffected.
 *  - Secrets: every byte goes through SecretScrubber before it reaches xterm.
 */
import { FitAddon } from '@xterm/addon-fit';
import { Terminal, type ITheme } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { useEffect, useMemo, useRef, type RefObject } from 'react';
import { SecretScrubber } from '../../lib/serial/scrub.ts';

/** MEMORY-SAFETY: bound on text handed to xterm but not yet parsed. */
const HIGH_WATER = 1_000_000;
/** How long a possible secret prefix is held back when the stream goes quiet. */
const SCRUB_HOLD_MS = 250;

export type NoteTone = 'tx' | 'info' | 'warn' | 'error';

export interface TerminalApi {
  /** raw board output (ANSI colours intact) */
  writeBytes(chunk: Uint8Array): void;
  /** a portal annotation, shown dimmed / coloured; never sent to the board */
  note(text: string, tone: NoteTone): void;
  clear(): void;
  setSecrets(values: readonly string[]): void;
  setScreenReaderMode(on: boolean): void;
  focus(): void;
}

const THEME: ITheme = {
  background: '#071423',
  foreground: '#d7e1ea',
  cursor: '#00b4d8',
  cursorAccent: '#071423',
  selectionBackground: '#1e4266',
  black: '#0d2137',
  // Portal TX notes use bright black: #94a7ba on #071423 is ~7:1 (SGR "dim" would halve it).
  brightBlack: '#94a7ba',
};

const TONE: Record<NoteTone, string> = {
  tx: '\x1b[90m→ ', // bright black (muted, but readable - not SGR dim)
  info: '\x1b[36m• ', // cyan
  warn: '\x1b[33m! ', // yellow
  error: '\x1b[31m✗ ', // red
};

interface EngineState {
  term: Terminal | null;
  readonly decoder: TextDecoder;
  readonly scrubber: SecretScrubber;
  queue: string[];
  raf: number;
  holdTimer: number;
  /** characters handed to xterm.write() whose parse callback has not fired yet */
  unparsed: number;
  dropped: number;
  atLineStart: boolean;
}

function createEngine(): EngineState {
  return {
    term: null,
    decoder: new TextDecoder('utf-8'),
    scrubber: new SecretScrubber(),
    queue: [],
    raf: 0,
    holdTimer: 0,
    unparsed: 0,
    dropped: 0,
    atLineStart: true,
  };
}

/**
 * The terminal is output-only (disableStdin): text for the board is typed into a
 * labelled input next to it, which screen readers and mobile keyboards handle far
 * better than a canvas terminal.
 */
export function useTerminal(hostRef: RefObject<HTMLDivElement | null>): TerminalApi {
  // Mutable engine state lives in a ref: bytes arrive at serial speed and must not re-render React.
  const engine = useRef<EngineState | null>(null);
  if (engine.current === null) engine.current = createEngine(); // lazy init (allowed during render)
  const st = engine as { readonly current: EngineState };

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const s = st.current;
    const term = new Terminal({
      convertEol: true,
      cursorBlink: false,
      cursorInactiveStyle: 'none',
      disableStdin: true,
      fontFamily: 'Cascadia Code, JetBrains Mono, Consolas, ui-monospace, monospace',
      fontSize: 13,
      scrollback: 5000,
      theme: THEME,
      // Firmware log colours (e.g. blue on navy) are lifted to WCAG AA contrast.
      minimumContrastRatio: 4.5,
      allowProposedApi: false,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    s.term = term;

    let fitRaf = 0;
    const refit = () => {
      cancelAnimationFrame(fitRaf);
      fitRaf = requestAnimationFrame(() => {
        // fit() throws while the element is display:none; the next resize retries.
        try {
          fit.fit();
        } catch {
          /* hidden */
        }
      });
    };
    refit();
    const ro = new ResizeObserver(refit);
    ro.observe(host);

    if (s.queue.length) schedule();

    return () => {
      ro.disconnect();
      cancelAnimationFrame(fitRaf);
      cancelAnimationFrame(s.raf);
      window.clearTimeout(s.holdTimer);
      term.dispose();
      s.term = null;
      s.raf = 0;
      s.unparsed = 0;
    };
    // hostRef is a stable ref object: the terminal lives exactly as long as the component.
  }, []);

  function schedule(): void {
    const s = st.current;
    if (s.raf || !s.term) return;
    s.raf = requestAnimationFrame(flush);
  }

  function flush(): void {
    const s = st.current;
    s.raf = 0;
    const term = s.term;
    if (!term || s.queue.length === 0) return;
    const text = s.queue.join('');
    s.queue = [];
    s.unparsed += text.length;
    term.write(text, () => {
      s.unparsed -= text.length;
    });
  }

  function enqueue(text: string): void {
    if (!text) return;
    const s = st.current;
    if (s.unparsed > HIGH_WATER) {
      s.dropped += text.length;
      return;
    }
    if (s.dropped > 0) {
      const kb = Math.ceil(s.dropped / 1024);
      s.dropped = 0;
      s.queue.push(`\r\n\x1b[33m! console skipped ~${kb} KB (output faster than the screen) - "Save log" has every line\x1b[0m\r\n`);
    }
    s.queue.push(text);
    s.atLineStart = text.endsWith('\n');
    schedule();
  }

  return useMemo<TerminalApi>(
    () => ({
      writeBytes(chunk) {
        const s = st.current;
        enqueue(s.scrubber.push(s.decoder.decode(chunk, { stream: true })));
        window.clearTimeout(s.holdTimer);
        s.holdTimer = window.setTimeout(() => enqueue(s.scrubber.flush()), SCRUB_HOLD_MS);
      },
      note(text, tone) {
        const s = st.current;
        const lead = s.atLineStart ? '' : '\r\n';
        enqueue(`${lead}${TONE[tone]}${s.scrubber.line(text)}\x1b[0m\r\n`);
      },
      clear() {
        const s = st.current;
        s.queue = [];
        s.term?.clear();
        s.atLineStart = true;
      },
      setSecrets(values) {
        st.current.scrubber.set(values);
      },
      setScreenReaderMode(on) {
        if (st.current.term) st.current.term.options.screenReaderMode = on;
      },
      focus() {
        st.current.term?.focus();
      },
    }),
    // enqueue/schedule only touch the engine ref, so the API object never changes.
    [],
  );
}
