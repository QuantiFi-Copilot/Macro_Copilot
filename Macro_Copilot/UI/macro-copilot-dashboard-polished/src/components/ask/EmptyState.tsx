// ============================================================================
// EmptyState — the calm-cockpit landing for the Ask surface
// ----------------------------------------------------------------------------
// Vertically centered (~38% from top) display headline + secondary line +
// 3 personalized starting points + 1 muted "browse the library" link.
//
// Starting points are rendered as typographic LINES (not chips, not
// cards).  Each begins with a thin `→` glyph and prose text in 14px
// Inter regular, fg-secondary, hover-tinted to ice-blue.  Clicking
// drops the prompt into the composer.
//
// The composer itself lives BELOW this component on the page; the
// empty state sits in a flex column with its own internal padding so
// the visual rhythm matches the brief.
// ============================================================================

import { Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { getStartingPoints } from './lib/startingPoints';

type Props = {
  onSelect: (prompt: string) => void;
};

export function EmptyState({ onSelect }: Props) {
  const points = getStartingPoints();

  return (
    <div className="flex h-full w-full flex-col items-center justify-center px-6 pb-12">
      <div className="w-full max-w-[720px] text-left">
        <h1 className="text-display-lg font-light text-fg-primary">
          Ask anything about macro.
        </h1>
        <p className="mt-2 text-[14px] text-fg-secondary">
          Every answer comes with the math, the methodology, and the lineage.
        </p>

        <div className="mt-12 space-y-3">
          {points.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => onSelect(p.prompt)}
              className="group flex w-full items-baseline gap-3 text-left transition-colors duration-150 ease-sleek"
            >
              <ArrowRight
                size={13}
                strokeWidth={1.5}
                className="mt-1 shrink-0 text-fg-faint transition-colors group-hover:text-ice-300"
              />
              <span className="text-[14px] leading-[1.55] text-fg-secondary transition-colors group-hover:text-fg-primary">
                {p.label}
              </span>
            </button>
          ))}
        </div>

        <Link
          to="/library"
          className="mt-10 inline-flex items-center gap-1.5 text-[12px] text-fg-muted transition-colors hover:text-fg-secondary"
        >
          or browse the library
          <ArrowRight size={11} className="text-fg-faint" />
        </Link>
      </div>
    </div>
  );
}
