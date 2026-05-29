// ============================================================================
// shared/build/elements/CountryCaveatBadge.tsx
// ----------------------------------------------------------------------------
// Registry-driven country-caveat card.  Two variants:
//
//   variant="card"   — full card with flag header + short label + caveat
//                       (used in the extended view's top-right slot)
//
//   variant="inline" — inline flag + one-line caveat (used in the
//                      compact view's footer)
//
// Finance-blind — looks up the curve_family in the shared registry and
// renders.  Per docs_revamped/03_standards/rendering_density.md §2.2
// hiding the caveat is forbidden; this component is the canonical
// surface for it.
// ============================================================================

import { Info } from 'lucide-react';
import { countryCaveatFor } from '../lib/countryCaveats';

type Props = {
  curveFamily: string;
  variant?: 'card' | 'inline';
};

export function CountryCaveatBadge({ curveFamily, variant = 'card' }: Props) {
  const entry = countryCaveatFor(curveFamily);
  if (!entry) return null;

  if (variant === 'inline') {
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] text-fg-secondary">
        <Info size={12} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
        <span>{entry.caveat}</span>
      </span>
    );
  }

  return (
    <div className="card flex h-full flex-col gap-2 px-4 py-3">
      <div className="kicker flex items-center gap-2 text-fg-muted">
        <Info size={12} strokeWidth={1.5} className="text-fg-faint" aria-hidden />
        <span>COUNTRY CAVEAT</span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-[18px] leading-none">{entry.flag}</span>
        <span className="text-[12px] font-medium text-fg-primary">
          {entry.shortLabel}
        </span>
      </div>
      <p className="text-[11.5px] leading-[1.45] text-fg-secondary">
        {entry.caveat}
      </p>
    </div>
  );
}
