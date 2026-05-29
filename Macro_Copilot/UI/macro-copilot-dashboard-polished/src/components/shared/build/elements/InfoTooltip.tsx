// ============================================================================
// shared/build/elements/InfoTooltip.tsx
// ----------------------------------------------------------------------------
// Inline (i) icon with a CSS-only tooltip on hover.  Used to surface
// methodology / disclosure text without taking space inline.
//
// Finance-blind — accepts any string content.  Per
// docs_revamped/03_standards/rendering_density.md §2.2 hiding
// methodology entirely is forbidden; this is the canonical compact-
// surface mechanism for surfacing it.
// ============================================================================

import { Info } from 'lucide-react';
import type { ReactNode } from 'react';

type Props = {
  /** The hover content — typically a one- or two-sentence caveat. */
  content: ReactNode;
  /** Optional accessible label override for the icon. */
  label?: string;
  /** Icon size in pixels (default 12 — fits in compact-card footer). */
  size?: number;
};

export function InfoTooltip({ content, label = 'More info', size = 12 }: Props) {
  return (
    <span className="group relative inline-flex items-center">
      <Info
        size={size}
        strokeWidth={1.5}
        className="text-fg-muted hover:text-fg-secondary"
        aria-label={label}
      />
      <span
        role="tooltip"
        className="pointer-events-none invisible absolute left-1/2 top-full z-50 mt-1 -translate-x-1/2 whitespace-pre-line rounded-md border border-line-subtle bg-surface-overlay px-3 py-2 text-[11px] leading-[1.5] text-fg-secondary opacity-0 shadow-overlay transition-opacity duration-150 group-hover:visible group-hover:opacity-100"
        style={{ maxWidth: '320px', width: 'max-content' }}
      >
        {content}
      </span>
    </span>
  );
}
