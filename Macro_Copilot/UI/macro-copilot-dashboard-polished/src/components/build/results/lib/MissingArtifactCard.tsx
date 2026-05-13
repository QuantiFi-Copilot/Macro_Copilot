// ============================================================================
// MissingArtifactCard — honest "this role didn't persist" placeholder.
// ----------------------------------------------------------------------------
// PR7 — when a specialised dashboard expects a role but the resolver
// finds no persisted node for it (execution failed mid-DAG, or the
// workspace pre-dates the role binding), the dashboard renders this
// card.  Never fabricates content; always names the missing role so
// the user knows what's absent.
// ============================================================================

import { AlertCircle } from 'lucide-react';

type Props = {
  roleLabel: string;
  reason?: string;
  /** Tailwind grid column-span class — keeps the card's layout
   *  consistent with the surrounding NodeWidgetCard grid. */
  spanClass?: string;
};

export function MissingArtifactCard({
  roleLabel,
  reason,
  spanClass = 'col-span-12 md:col-span-6',
}: Props) {
  return (
    <div className={spanClass}>
      <div className="card flex items-start gap-3 border-amber-400/25 bg-amber-500/[0.04] px-4 py-3">
        <AlertCircle
          size={13}
          className="mt-0.5 shrink-0 text-amber-300"
          aria-hidden
        />
        <div className="min-w-0">
          <div className="text-[11.5px] font-semibold text-fg-primary">
            Missing: {roleLabel}
          </div>
          <p className="mt-1 text-[10.5px] leading-[1.5] text-fg-secondary">
            {reason ??
              'This workflow stage didn’t persist an artifact.  The dashboard is rendering the parts that did.'}
          </p>
        </div>
      </div>
    </div>
  );
}
