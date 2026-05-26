// ============================================================================
// MethodologyPanel — what the model does + assumptions + locked vs
//                    configurable knobs (collapsible sections)
// ----------------------------------------------------------------------------
// Reads from the ToolCard's methodology block (what_it_does, assumptions,
// citations, planned_extensions) and conventions (per-knob value + source +
// rationale).  This is the panel the regulator / PM looks at when they want
// to see "what is this thing actually doing?".
// ============================================================================

import { useState } from 'react';
import { ChevronDown, ChevronRight, Settings2 } from 'lucide-react';
import type { ToolCard } from '@/types/workflows';
import { cn } from '@/utils/cn';

export function MethodologyPanel({ card }: { card: ToolCard }) {
  return (
    <div className="space-y-3">
      <Section title="What it does" defaultOpen>
        <p className="text-[12px] leading-[1.55] text-fg-secondary">
          {card.methodology.what_it_does}
        </p>
      </Section>

      {card.methodology.assumptions.length > 0 ? (
        <Section title={`Assumptions (${card.methodology.assumptions.length})`}>
          <ul className="space-y-1">
            {card.methodology.assumptions.map((a, i) => (
              <li
                key={i}
                className="flex gap-2 text-[11.5px] leading-[1.5] text-fg-secondary"
              >
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-fg-faint" />
                <span>{a}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {card.conventions.length > 0 ? (
        <Section
          title={`Conventions · locked knobs (${card.conventions.length})`}
        >
          <p className="mb-2 text-[10.5px] leading-snug text-fg-faint">
            Defaults baked into this primitive — sourced from{' '}
            <code className="mono text-ice-300">config.yaml</code>. Each entry
            has a rationale so you can see <em>why</em> it was set this way.
          </p>
          <div className="space-y-1.5">
            {card.conventions.map((c) => (
              <div
                key={c.name}
                className="rounded-md border border-line-subtle bg-white/[0.012] px-2.5 py-2"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="mono text-[10.5px] text-ice-200">
                    {c.name}
                  </span>
                  <span className="mono shrink-0 text-[10.5px] text-fg-primary">
                    {String(c.value)}
                  </span>
                </div>
                <p className="mt-1 text-[10px] leading-snug text-fg-faint">
                  <span className="uppercase tracking-[0.1em]">
                    {c.source}
                  </span>
                  {' · '}
                  {c.rationale}
                </p>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {card.methodology.citations.length > 0 ? (
        <Section title={`Citations (${card.methodology.citations.length})`}>
          <ul className="space-y-1">
            {card.methodology.citations.map((c, i) => (
              <li
                key={i}
                className="text-[11.5px] leading-[1.5] text-fg-secondary"
              >
                {c}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {card.methodology.planned_extensions.length > 0 ? (
        <Section
          title={`Planned extensions (${card.methodology.planned_extensions.length})`}
        >
          <ul className="space-y-1">
            {card.methodology.planned_extensions.map((p, i) => (
              <li
                key={i}
                className="flex gap-2 text-[11.5px] leading-[1.5] text-fg-secondary"
              >
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-fg-faint" />
                <span>{p}</span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

function Section({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-line-subtle bg-white/[0.008] px-3 py-2.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 text-left"
      >
        {open ? (
          <ChevronDown size={11} className="text-fg-faint" />
        ) : (
          <ChevronRight size={11} className="text-fg-faint" />
        )}
        <Settings2 size={11} className="text-ice-300/70" />
        <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-fg-secondary">
          {title}
        </span>
      </button>
      {open ? <div className="mt-2.5">{children}</div> : null}
    </div>
  );
}
