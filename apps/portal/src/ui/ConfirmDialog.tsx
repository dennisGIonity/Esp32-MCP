/*
 * AEDI - IONITY GLOBAL | @ionity/portal | ConfirmDialog.tsx | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Confirmation for destructive actions (factory reset, erase flash), in line
 * with the project rule "confirm every risky command". Native <dialog> with
 * showModal(): focus moves into it, the page behind is inert, Esc cancels.
 * The safe choice (Cancel) is first and receives focus.
 */
import { useEffect, useId, useRef, type ReactNode } from 'react';

interface Props {
  readonly open: boolean;
  readonly title: string;
  readonly children: ReactNode;
  readonly confirmLabel: string;
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
}

export function ConfirmDialog({ open, title, children, confirmLabel, onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const bodyId = useId();

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onCancel={(e) => {
        e.preventDefault(); // keep React state the single source of truth
        onCancel();
      }}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-xl border border-slate-300 bg-white p-0 text-slate-900 shadow-xl backdrop:bg-navy-950/60 dark:border-navy-700 dark:bg-navy-900 dark:text-slate-100"
    >
      <div className="flex flex-col gap-4 p-5">
        <h2 id={titleId} className="text-lg font-bold">
          {title}
        </h2>
        <div id={bodyId} className="text-sm">
          {children}
        </div>
        <div className="flex flex-wrap justify-end gap-3">
          <button type="button" className="btn-secondary" onClick={onCancel} autoFocus>
            Cancel
          </button>
          <button type="button" className="btn bg-bad text-white hover:bg-red-800" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
