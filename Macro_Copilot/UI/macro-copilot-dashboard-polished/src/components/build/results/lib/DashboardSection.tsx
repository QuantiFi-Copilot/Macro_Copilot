// ============================================================================
// DashboardSection — shared chrome for PR7 workflow dashboards.
// ----------------------------------------------------------------------------
// Renders a labelled section with a count badge + optional caption.
// Used by every workflow-specific dashboard so the visual register
// stays identical across (and consistent with the pre-PR7 generic
// grid's ``SectionHeader``).
// ============================================================================

import type { ReactNode } from 'react';

type Props = {
  label: string;
  description?: string;
  count?: number;
  countLabel?: string;
  children: ReactNode;
};

export function DashboardSection({
  label,
  description,
  count,
  countLabel,
  children,
}: Props) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3 px-0.5">
        <div className="min-w-0">
          <h2 className="kicker text-fg-muted">{label}</h2>
          {description && (
            <p className="mt-0.5 text-[11px] text-fg-faint">{description}</p>
          )}
        </div>
        {count !== undefined && (
          <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.16em] text-fg-faint">
            {count} {countLabel ?? (count === 1 ? 'item' : 'items')}
          </span>
        )}
      </div>
      {children}
    </section>
  );
}
