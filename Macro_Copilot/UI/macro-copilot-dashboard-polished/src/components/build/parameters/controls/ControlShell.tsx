// ============================================================================
// ControlShell — wraps every parameter control with consistent chrome.
// ----------------------------------------------------------------------------
// Renders the kicker label + help caption + override-state ribbon
// around any per-kind control component.  Lives in shared/ so every
// control gets the same vertical rhythm + the same "modified from
// parent" affordance.
//
// Override states (visual):
//   - none           → no ribbon, current value rendered cleanly
//   - pending        → 4px violet bar on the left + small "← was X"
//                      caption beneath the field
//   - readOnly       → 4px grey bar + a lock icon next to the label
//   - locked         → identical to readOnly visually but the label
//                      includes a "topology-locked" suffix
// ============================================================================

import { Lock, RotateCcw } from 'lucide-react';
import { cn } from '@/utils/cn';
import type {
  ParamControlDescriptor,
  ParamOverride,
} from '../lib/controlSchema';

type Props = {
  descriptor: ParamControlDescriptor;
  override: ParamOverride | undefined;
  /** Called when the user clicks the "revert" affordance on a
   *  pending override.  Signals the parent to clear this descriptor's
   *  override entry. */
  onRevert?: () => void;
  children: React.ReactNode;
};

export function ControlShell({
  descriptor,
  override,
  onRevert,
  children,
}: Props) {
  const isPending = !!override;
  const isReadOnly = !!descriptor.readOnly;

  const stripeClass = isPending
    ? 'before:bg-violet-400/60'
    : isReadOnly
      ? 'before:bg-line-soft'
      : 'before:bg-transparent';

  return (
    <div
      className={cn(
        'relative pl-3 transition-colors',
        'before:absolute before:left-0 before:top-2 before:h-[calc(100%-1rem)] before:w-[2px] before:rounded-full',
        stripeClass,
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <label className="truncate text-[10.5px] font-medium uppercase tracking-[0.14em] text-fg-muted">
            {descriptor.label}
          </label>
          {isReadOnly && (
            <Lock size={9} strokeWidth={2} className="shrink-0 text-fg-faint" />
          )}
        </div>
        {isPending && onRevert && (
          <button
            type="button"
            onClick={onRevert}
            title="Revert to parent value"
            className="flex items-center gap-1 rounded-sm border border-line-soft px-1.5 py-px text-[9.5px] font-medium text-fg-muted transition-colors hover:border-ice-400/30 hover:text-ice-200"
          >
            <RotateCcw size={9} strokeWidth={2} />
            <span>Revert</span>
          </button>
        )}
      </div>
      <div className="mt-1">{children}</div>
      {descriptor.helpText && (
        <p className="mt-1 text-[10.5px] leading-[1.4] text-fg-faint">
          {descriptor.helpText}
        </p>
      )}
      {isPending && override && (
        <p className="mt-1 text-[10.5px] text-violet-300">
          ← was{' '}
          <span className="font-mono">{formatPreviousValue(override)}</span>
        </p>
      )}
    </div>
  );
}

function formatPreviousValue(override: ParamOverride): string {
  const v = override.previousValue;
  if (v == null) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
