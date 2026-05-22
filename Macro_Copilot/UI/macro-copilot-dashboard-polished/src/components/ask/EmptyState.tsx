// ============================================================================
// EmptyState — the calm-cockpit landing for the Ask surface
// ----------------------------------------------------------------------------
// 2026 editorial register: Inter sans for the body of the headline, with
// a single Instrument Serif italic on "anything" — a one-word editorial
// accent that signals the page is considered, not generic.  Behind the
// headline sits an `.ambient-orb` (soft ice/violet halo) anchored
// slightly above optical center.
//
// Starting points are typographic LINES, not chips.  Each line gets a
// thin `→` glyph that animates on hover with a small rightward slide;
// the type lifts in tone but doesn't change weight, so the page reads
// as calm motion, not a button-y press.
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
    <div className="relative flex h-full w-full flex-col items-center justify-center px-6 pb-12">
      {/* Ambient orb anchored above optical center.  Sits behind the
          headline, soft enough that it reads as light spilling onto
          the page rather than a discrete graphic. */}
      <div
        aria-hidden
        className="ambient-orb"
        style={{
          left: 'calc(50% - 360px)',
          top: 'calc(34% - 360px)',
        }}
      />

      <div className="relative z-10 w-full max-w-[760px] text-left">
        <h1 className="text-[44px] font-light leading-[1.05] tracking-[-0.02em] text-fg-primary">
          Ask{' '}
          <span className="font-serif-display italic text-[1.05em] font-normal text-ice-100">
            anything
          </span>{' '}
          about macro.
        </h1>
        <p className="mt-3 text-[14.5px] leading-[1.55] tracking-[-0.005em] text-fg-secondary">
          Every answer comes with the math, the methodology, and the lineage —
          rendered as a graph, not just prose.
        </p>

        <div className="mt-12 space-y-3.5">
          {points.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => onSelect(p.prompt)}
              className="group flex w-full items-baseline gap-3.5 text-left"
            >
              <ArrowRight
                size={13}
                strokeWidth={1.5}
                className="mt-1 shrink-0 text-fg-faint transition-all duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] group-hover:translate-x-0.5 group-hover:text-ice-300"
              />
              <span className="text-[14px] leading-[1.55] tracking-[-0.005em] text-fg-secondary transition-colors duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] group-hover:text-fg-primary">
                {p.label}
              </span>
            </button>
          ))}
        </div>

        <div className="mt-12 flex items-center gap-3">
          <span className="h-px flex-1 bg-gradient-to-r from-transparent via-line-soft to-transparent" />
          <Link
            to="/library"
            className="group inline-flex items-center gap-1.5 text-[11.5px] font-medium uppercase tracking-[0.16em] text-fg-faint transition-colors duration-200 hover:text-fg-secondary"
          >
            <span>or browse the library</span>
            <ArrowRight
              size={11}
              className="transition-transform duration-200 group-hover:translate-x-0.5"
            />
          </Link>
          <span className="h-px flex-1 bg-gradient-to-r from-transparent via-line-soft to-transparent" />
        </div>
      </div>
    </div>
  );
}
