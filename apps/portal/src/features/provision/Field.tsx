/*
 * AEDI - IONITY GLOBAL | @ionity/portal | Field.tsx | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * One labelled form control: label above the control, optional hint, and an
 * error message wired with aria-invalid / aria-errormessage / aria-describedby.
 * The error element is always rendered (empty when valid) so the references
 * never point at a missing id.
 */
import { useId, type ReactNode } from 'react';

export interface FieldIds {
  readonly id: string;
  readonly hintId: string;
  readonly errorId: string;
}

/** Props to spread on the input/textarea/select inside a Field. */
export function controlProps(ids: FieldIds, error: string | undefined, hasHint: boolean) {
  return {
    id: ids.id,
    'aria-invalid': error ? (true as const) : (false as const),
    'aria-errormessage': error ? ids.errorId : undefined,
    'aria-describedby': [hasHint ? ids.hintId : '', error ? ids.errorId : ''].filter(Boolean).join(' ') || undefined,
  };
}

export function useFieldIds(): FieldIds {
  const base = useId();
  return { id: `${base}-control`, hintId: `${base}-hint`, errorId: `${base}-error` };
}

interface FieldProps {
  readonly ids: FieldIds;
  readonly label: string;
  readonly hint?: ReactNode;
  readonly error?: string | undefined;
  readonly optional?: boolean;
  /** the control, built with controlProps(ids, error, !!hint) */
  readonly children: ReactNode;
  /** buttons next to the control (Show / Scan / Generate) */
  readonly addon?: ReactNode;
}

export function Field({ ids, label, hint, error, optional, children, addon }: FieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={ids.id} className="text-sm font-semibold text-navy-900 dark:text-slate-100">
        {label}
        {optional ? <span className="ml-1 font-normal text-slate-600 dark:text-slate-400">(optional)</span> : null}
      </label>
      {hint ? (
        <p id={ids.hintId} className="text-sm text-slate-600 dark:text-slate-300">
          {hint}
        </p>
      ) : null}
      <div className="flex items-stretch gap-2">
        <div className="min-w-0 flex-1">{children}</div>
        {addon}
      </div>
      <p id={ids.errorId} className="text-sm font-medium text-bad empty:hidden dark:text-red-300">
        {error ?? ''}
      </p>
    </div>
  );
}
