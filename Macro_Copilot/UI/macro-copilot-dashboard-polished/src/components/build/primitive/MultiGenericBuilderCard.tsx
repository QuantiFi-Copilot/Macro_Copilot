// ============================================================================
// MultiGenericBuilderCard — comparison-grid tile for PR2 generic builders.
// ----------------------------------------------------------------------------
// Sibling of ``MultiPrimitiveCard`` (typed views) and
// ``MultiUnsupportedKnownCard`` (paused tools).  Used by
// ``MultiPrimitiveCanvas`` when a multi-tool Ask hand-off includes a
// primitive that's runnable but has no bespoke typed view yet (OIS
// curve spread, swap spread, breakeven, sovereign yield panel, etc.).
//
// Click → opens the single-builder URL with the tile's params dict
// pre-filled.  Same ``?context=`` shape ``GenericPrimitiveBuilder``
// reads on mount so the form picks the values up.
// ============================================================================

import { ArrowUpRight, Wrench } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { encodeGenericBuilderContext } from './GenericPrimitiveBuilder';

type Props = {
  toolName: string;
  params: Record<string, string>;
};

export function MultiGenericBuilderCard({ toolName, params }: Props) {
  const navigate = useNavigate();

  const handleOpen = () => {
    navigate(`/workspace?context=${encodeGenericBuilderContext(toolName, params)}`);
  };

  const paramPairs = Object.entries(params).filter(([, v]) => v && v.length > 0);

  return (
    <button
      type="button"
      onClick={handleOpen}
      className="research-card group relative flex min-h-[180px] flex-col gap-3 overflow-hidden px-4 py-3.5 text-left transition-transform duration-200 ease-sleek hover:-translate-y-px focus-visible:-translate-y-px focus-visible:outline-none"
      style={{ ['--rail-color' as string]: 'rgba(106, 192, 245, 0.55)' /* ice */ }}
    >
      <span aria-hidden className="research-card-rail" />

      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span className="kicker text-ice-300">Builder · schema-driven</span>
          <h4 className="mt-0.5 truncate text-[13px] font-semibold tracking-[-0.008em] text-fg-primary">
            {humanLabel(toolName.replace(/_tool$/, ''))}
          </h4>
          <div className="mt-0.5 truncate font-mono text-[10px] text-fg-muted">
            {toolName}
          </div>
        </div>
        <span
          aria-hidden
          className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-ice-400/30 bg-ice-500/10 text-ice-300"
        >
          <Wrench size={11} strokeWidth={1.75} />
        </span>
      </div>

      <p className="line-clamp-3 text-[11px] leading-[1.5] text-fg-secondary">
        Configure parameters from the tool&apos;s schema, then run it
        through the backend primitive surface.  Output renders via the
        auto-renderer (KPI + time-series).
      </p>

      <div className="mt-auto flex flex-col gap-1.5 pt-1">
        {paramPairs.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {paramPairs.slice(0, 4).map(([k, v]) => (
              <span
                key={k}
                className="inline-flex items-center gap-1 rounded-sm border border-line-soft bg-white/[0.025] px-1.5 py-0.5 font-mono text-[9.5px] text-fg-secondary"
              >
                <span className="text-fg-faint">{k}</span>
                <span className="text-fg-muted">=</span>
                <span>{v}</span>
              </span>
            ))}
            {paramPairs.length > 4 ? (
              <span className="font-mono text-[9.5px] text-fg-faint">
                +{paramPairs.length - 4} more
              </span>
            ) : null}
          </div>
        ) : (
          <span className="text-[10.5px] text-fg-faint">
            No params supplied — opens with schema defaults
          </span>
        )}
        <div className="flex items-center justify-end">
          <ArrowUpRight
            size={11}
            strokeWidth={1.75}
            aria-hidden
            className="shrink-0 text-fg-faint transition-colors group-hover:text-ice-200"
          />
        </div>
      </div>
    </button>
  );
}

function humanLabel(snake: string): string {
  return snake
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
