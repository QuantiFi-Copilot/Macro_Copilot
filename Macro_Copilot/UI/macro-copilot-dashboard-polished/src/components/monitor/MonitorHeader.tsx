// ============================================================================
// MonitorHeader — top strip on Monitor / Rates Agent surfaces
// ----------------------------------------------------------------------------
// Three regions:
//   - LEFT: kicker date strip (mono uppercase) + a single-line headline
//     with one Instrument Serif italic accent (the page's editorial
//     moment), plus an optional sub-line.
//   - RIGHT: edit-mode toggle ("Customize" / "Done") with a small
//     reset-to-default action when in edit mode.
//
// The headline is a derived factual line — flagged-extreme count for
// the Monitor surface, instrument scope for Rates Agent.  Replaces the
// "Good morning, Sreeram" greeting from V0.
// ============================================================================

import { Pencil, RotateCcw, Check } from 'lucide-react';
import { cn } from '@/utils/cn';
import { AsOfControl } from './AsOfControl';

type Props = {
  kicker: string;
  /** Headline plain text BEFORE the serif accent (e.g. "Three"). */
  headlinePrefix: string;
  /** Headline accent — rendered in Instrument Serif italic. */
  headlineAccent: string;
  /** Headline plain text AFTER the serif accent. */
  headlineSuffix: string;
  /** Optional muted sub-line below the headline. */
  subline?: string;
  isEditing: boolean;
  onToggleEdit: () => void;
  onResetDefault?: () => void;
};

export function MonitorHeader({
  kicker,
  headlinePrefix,
  headlineAccent,
  headlineSuffix,
  subline,
  isEditing,
  onToggleEdit,
  onResetDefault,
}: Props) {
  return (
    <div className="mb-7 flex items-end justify-between gap-6">
      <div className="min-w-0">
        <p className="font-mono text-[10.5px] font-medium uppercase tracking-[0.16em] text-fg-muted">
          {kicker}
        </p>
        <h1 className="mt-2.5 text-[30px] font-light leading-[1.1] tracking-[-0.018em] text-fg-primary">
          {headlinePrefix}
          {headlineAccent && (
            <>
              {headlinePrefix && ' '}
              <span className="font-serif-display text-[1.05em] font-normal italic text-ice-100">
                {headlineAccent}
              </span>
              {headlineSuffix && ' '}
            </>
          )}
          {headlineSuffix}
        </h1>
        {subline && (
          <p className="mt-1.5 text-[13px] leading-[1.55] text-fg-secondary">
            {subline}
          </p>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1.5">
        <AsOfControl />
        {isEditing && onResetDefault && (
          <button
            type="button"
            onClick={onResetDefault}
            className="flex h-8 items-center gap-1.5 rounded-md px-2.5 text-[11.5px] font-medium text-fg-secondary transition-colors hover:bg-white/[0.025] hover:text-fg-primary"
            title="Reset to default layout"
          >
            <RotateCcw size={11} />
            <span>Reset</span>
          </button>
        )}
        <button
          type="button"
          onClick={onToggleEdit}
          className={cn(
            'flex h-8 items-center gap-1.5 rounded-md px-3 text-[12px] font-medium transition-all duration-200 ease-sleek',
            isEditing
              ? 'composer-send-active'
              : 'text-fg-secondary ring-1 ring-line-soft hover:bg-white/[0.025] hover:text-fg-primary',
          )}
        >
          {isEditing ? <Check size={11} strokeWidth={2.25} /> : <Pencil size={11} />}
          <span>{isEditing ? 'Done' : 'Customize'}</span>
        </button>
      </div>
    </div>
  );
}
