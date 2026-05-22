// ============================================================================
// MultiUnsupportedKnownCard — compact tile for unsupported-known tools.
// ----------------------------------------------------------------------------
// PR1 — Build Routing Coverage.  Mirrors ``MultiPrimitiveCard``'s
// chassis but for the ``unsupported_known`` decoded variant.  Renders
// a short "this tool is known but not yet renderable" tile inside the
// multi-card comparison grid so the user can see which tools in their
// Ask answer DO have a Build view and which don't — instead of having
// the unsupported tools silently dropped from the grid.
//
// Click → opens the single-card canvas, which mounts
// ``UnsupportedKnownToolCanvas`` for the full per-tool reason / what-
// works-now hint / "Try in Ask" affordance.
// ============================================================================

import { Construction, ArrowUpRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { unsupportedKnownReasonFor } from '@/lib/toolNames';

type Props = {
  toolName: string;
  params: Record<string, string>;
};

export function MultiUnsupportedKnownCard({ toolName, params }: Props) {
  const navigate = useNavigate();
  const reason = unsupportedKnownReasonFor(toolName);

  const handleOpen = () => {
    const ctx = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: toolName, params }],
        tool_count: 1,
      }),
    );
    navigate(`/workspace?context=${ctx}`);
  };

  return (
    <button
      type="button"
      onClick={handleOpen}
      className="research-card group relative flex min-h-[180px] flex-col gap-3 overflow-hidden px-4 py-3.5 text-left transition-transform duration-200 ease-sleek hover:-translate-y-px focus-visible:-translate-y-px focus-visible:outline-none"
      style={{ ['--rail-color' as string]: 'rgba(243, 183, 85, 0.5)' /* amber */ }}
    >
      <span aria-hidden className="research-card-rail" />

      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span className="kicker text-amber-300">Known · unsupported</span>
          <h4 className="mt-0.5 truncate text-[13px] font-semibold tracking-[-0.008em] text-fg-primary">
            {reason.label}
          </h4>
          <div className="mt-0.5 truncate font-mono text-[10px] text-fg-muted">
            {toolName}
          </div>
        </div>
        <span
          aria-hidden
          className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-amber-400/30 bg-amber-500/10 text-amber-300"
        >
          <Construction size={11} strokeWidth={1.75} />
        </span>
      </div>

      <p className="line-clamp-3 text-[11px] leading-[1.5] text-fg-secondary">
        {reason.reason}
      </p>

      <div className="mt-auto flex items-center justify-between gap-2 pt-1">
        <span className="text-[10.5px] text-fg-faint">{reason.whatWorksNow}</span>
        <ArrowUpRight
          size={11}
          strokeWidth={1.75}
          aria-hidden
          className="shrink-0 text-fg-faint transition-colors group-hover:text-ice-200"
        />
      </div>
    </button>
  );
}
