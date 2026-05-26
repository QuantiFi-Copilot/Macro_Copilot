// ============================================================================
// WorkspaceButton — the "See more in workspace" CTA on legacy chat-drawer
// research cards.
// ----------------------------------------------------------------------------
// PR1 — every known tool name is normalised before the context blob is
// emitted, so manifest shorthand (``half_life_tool`` etc.) reaches
// Build's ``contextDecoder`` in canonical form (``calculate_half_life_
// tool``).  Multi-tool contexts are preserved; the destination canvas
// (``MultiPrimitiveCanvas``) splits typed primitives from
// unsupported-known cells per PR1.
// ============================================================================

import { ArrowUpRight, BarChart3 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { WorkspaceContext } from '@/types/copilot';
import { normalizeToolName } from '@/lib/toolNames';
import { getPrimitiveModule } from '@/modules';

type WorkspaceButtonProps = {
  context: WorkspaceContext;
};

// Stage 4d — per-tool labels for the workspace button subtitle are
// now sourced from each module's ``workspaceLabel`` field on its
// spec.  Falls back to the raw tool name when the owning module
// doesn't ship a rich label (so multi-tool contexts that include
// generic-builder tools without bespoke copy still render meaningful
// text).  This file used to carry a hand-authored
// ``TOOL_WORKSPACE_LABELS`` record (9 entries); each entry moved
// onto its owning module's spec.
function workspaceLabelFor(toolName: string): string {
  return getPrimitiveModule(toolName)?.workspaceLabel ?? toolName;
}

export function WorkspaceButton({ context }: WorkspaceButtonProps) {
  const navigate = useNavigate();

  // PR1 — normalise every tool name so multi-tool contexts that mix
  // manifest shorthand + canonical names all reach the decoder in the
  // expected form.  Preserves the original tool order so multi-card
  // grids render the user's intended comparison left-to-right.
  const normalisedContext: WorkspaceContext = {
    ...context,
    tools: context.tools.map((t) => ({
      ...t,
      tool: normalizeToolName(t.tool),
    })),
  };

  const subtitle = normalisedContext.tools
    .map((t) => workspaceLabelFor(t.tool))
    .join(' · ');

  const handleClick = () => {
    // SPA navigation — Build's ``BuildShell`` reads the ``?context=``
    // param and decodes it into the right surface (single canvas,
    // multi-card grid, builder redirect, or unsupported-known card).
    const encoded = encodeURIComponent(JSON.stringify(normalisedContext));
    navigate(`/workspace?context=${encoded}`);
  };

  return (
    <button
      onClick={handleClick}
      className="group mt-3 flex w-full items-center gap-3 rounded-lg border border-ice-400/20 bg-ice-500/[0.06] px-3.5 py-2.5 text-left transition-all duration-150 ease-sleek hover:border-ice-400/35 hover:bg-ice-500/[0.1]"
    >
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-ice-400/25 bg-ice-500/15">
        <BarChart3 size={13} className="text-ice-300" />
      </div>
      <div className="min-w-0 flex-1">
        <span className="text-[12px] font-semibold text-ice-200">
          See more in workspace
        </span>
        <span className="mt-0.5 block truncate text-[10px] text-ice-300/60">
          {subtitle}
        </span>
      </div>
      <ArrowUpRight
        size={13}
        className="shrink-0 text-ice-300/40 transition-colors group-hover:text-ice-300"
      />
    </button>
  );
}
