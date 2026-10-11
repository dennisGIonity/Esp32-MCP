/*
 * AEDI - IONITY GLOBAL | @ionity/portal | PairingChecklist.tsx | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The pairing steps as an ordered list. State is carried by text (and an icon
 * marked aria-hidden), never by colour alone. Progress is announced through the
 * app's live regions by the flow hook, not by making this list live.
 */
import type { FlowState, StepState } from './usePairingFlow.ts';

const ICON: Record<StepState, string> = { pending: '○', active: '◐', done: '✓', failed: '✗', skipped: '–' };
const WORD: Record<StepState, string> = { pending: 'not started', active: 'in progress', done: 'done', failed: 'failed', skipped: 'skipped' };
const TONE: Record<StepState, string> = {
  pending: 'text-slate-500 dark:text-slate-400',
  active: 'text-cyan-ink dark:text-cyan-brand',
  done: 'text-ok dark:text-green-400',
  failed: 'text-bad dark:text-red-300',
  skipped: 'text-slate-500 dark:text-slate-400',
};

export function PairingChecklist({ flow, onStop }: { readonly flow: FlowState; readonly onStop: () => void }) {
  const visible = flow.running || flow.outcome !== 'idle';
  if (!visible) return null;
  return (
    <section className="card" aria-labelledby="pairing-title" data-testid="pairing">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 id="pairing-title" className="text-lg font-bold text-navy-900 dark:text-slate-100">
          Pairing progress
        </h2>
        {flow.running ? (
          <button type="button" className="btn-secondary" onClick={onStop}>
            Stop
          </button>
        ) : null}
      </div>
      <ol className="flex flex-col gap-2">
        {flow.steps.map((s) => (
          <li key={s.id} className="flex items-start gap-3" aria-current={s.state === 'active' ? 'step' : undefined} data-step={s.id} data-state={s.state}>
            <span aria-hidden="true" className={`mt-0.5 w-5 shrink-0 text-center text-lg leading-5 ${TONE[s.state]} ${s.state === 'active' ? 'motion-safe:animate-pulse' : ''}`}>
              {ICON[s.state]}
            </span>
            <span className="min-w-0">
              <span className="font-medium">{s.label}</span>
              <span className={`ml-2 text-sm ${TONE[s.state]}`}>({WORD[s.state]})</span>
              {s.detail ? <span className="block break-words text-sm text-slate-600 dark:text-slate-300">{s.detail}</span> : null}
            </span>
          </li>
        ))}
      </ol>
      {flow.message ? (
        <p
          className={`mt-4 rounded-md p-3 text-sm font-medium ${
            flow.outcome === 'success' ? 'bg-green-50 text-green-900 dark:bg-green-950/50 dark:text-green-200' : 'bg-amber-50 text-amber-950 dark:bg-amber-950/40 dark:text-amber-100'
          }`}
          data-testid="pairing-outcome"
          data-outcome={flow.outcome}
        >
          {flow.message}
        </p>
      ) : null}
    </section>
  );
}
