// ============================================================================
// PrimitiveCanvasShell — shared chassis for typed primitive views.
// ----------------------------------------------------------------------------
// Every typed primitive view (Spread, CrossMarket, Butterfly, Yield, Regime,
// Scanner, Forward) renders inside this shell so the Build surface reads
// consistently when a user lands via Ask→Build for a single-primitive call:
//
//   ┌──────────────────────────────────────────────────────────────┐
//   │ <kicker: PRIMITIVE>                                          │
//   │ <Title>                       <as-of date / status pills>    │
//   │ <subtitle>                                                   │
//   └──────────────────────────────────────────────────────────────┘
//   ┌──────────────────────────────────────────────────────────────┐
//   │ <body — chart + metrics or whatever the view renders>        │
//   └──────────────────────────────────────────────────────────────┘
//   ┌──────────────────────────────────────────────────────────────┐
//   │ Methodology  (toggleable card)                              │
//   └──────────────────────────────────────────────────────────────┘
//
// The shell pulls together the editorial header pattern Library and Monitor
// use, so a virtual primitive canvas reads as a first-class Build artifact
// rather than a query-param data dump.
// ============================================================================

import { ChevronRight } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { cn } from '@/utils/cn';

type Props = {
  /** Kicker label above the headline — typically the column vocabulary
   *  used elsewhere on Build ("Primitive" / "Operator" / "Output"). */
  kicker: string;
  title: string;
  subtitle?: string;
  asOfDate?: string | null;
  /** Right-side meta strip — e.g. tool-name chip, lineage chip. */
  meta?: ReactNode;
  /** Methodology blurb shown in a collapsible card at the bottom of the
   *  view.  Omit to drop the section entirely. */
  methodology?: ReactNode;
  /** Body content — chart, table, metrics grid. */
  children: ReactNode;
};

export function PrimitiveCanvasShell({
  kicker,
  title,
  subtitle,
  asOfDate,
  meta,
  methodology,
  children,
}: Props) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="flex shrink-0 flex-col gap-2 border-b border-line-subtle px-6 pt-5 pb-4">
        <div className="flex items-baseline justify-between gap-4">
          <div className="min-w-0 flex-1">
            <p className="kicker text-fg-muted">{kicker}</p>
            <h1 className="mt-1 truncate text-[22px] font-medium tracking-[-0.012em] text-fg-primary">
              {title}
            </h1>
            {subtitle && (
              <p className="mt-1 text-[12px] leading-[1.5] text-fg-secondary">
                {subtitle}
              </p>
            )}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            {asOfDate && (
              <span className="font-mono text-[10px] text-fg-faint">
                as of {asOfDate}
              </span>
            )}
            {meta && <div className="flex items-center gap-2">{meta}</div>}
          </div>
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-5 px-6 py-5">
        {children}
        {methodology && <MethodologyCard>{methodology}</MethodologyCard>}
      </div>
    </div>
  );
}

function MethodologyCard({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="research-card overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group flex w-full items-center justify-between gap-3 px-5 py-3 text-left transition-colors hover:bg-white/[0.012]"
        aria-expanded={open}
      >
        <span className="kicker text-fg-muted">Methodology</span>
        <ChevronRight
          size={12}
          strokeWidth={1.75}
          className={cn(
            'shrink-0 text-fg-faint transition-transform duration-200 ease-sleek',
            open ? 'rotate-90 text-ice-200' : 'group-hover:text-ice-200',
          )}
        />
      </button>
      {open && (
        <div className="border-t border-line-subtle px-5 py-4 text-[12px] leading-[1.55] text-fg-secondary">
          {children}
        </div>
      )}
    </section>
  );
}

/** Inline tool-name chip — used by every view in its header meta slot
 *  so the chassis carries the underlying primitive identity at a
 *  glance. */
export function ToolNameChip({ tool }: { tool: string }) {
  return (
    <span
      className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-faint"
      title={tool}
    >
      {tool}
    </span>
  );
}
