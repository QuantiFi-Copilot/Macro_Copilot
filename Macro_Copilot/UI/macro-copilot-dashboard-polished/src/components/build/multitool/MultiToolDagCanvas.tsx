// ============================================================================
// MultiToolDagCanvas — generic multi-tool DAG page (Stage D).
// ----------------------------------------------------------------------------
// Replaces the legacy ``MultiPrimitiveCanvas`` as the surface BuildShell mounts
// when a ``?context=`` decodes to ≥2 tool calls.  This is the SINGLE page that
// renders EVERY multi-tool prompt from now on, regardless of which tools are
// involved — it is TOOL-AGNOSTIC end to end:
//
//   - the pure ``dagModel`` builds nodes/edges/archetype from the generic
//     decode (no tool names hardcoded);
//   - each node renders its module's ``surfaces.buildCompact`` via the
//     registry (``DagNodeBody``), with a legacy fallback;
//   - the shared ``ExpandedViewProvider`` hosts the click-to-expand modal that
//     mounts any tool's ``surfaces.buildExtended``.
//
// Layout (Stage-D plan + multi_tool.png mockup + the tabs the user asked for —
// most analysts read the data, not the topology, so the DAG is opt-in):
//   ┌ DagHeaderStrip — query / prompt + summary + "stitched by AI"
//   ├ DagTabBar      — "Cards" (default) | "DAG" (shown when N ≥ 2)
//   └ tab body:
//       • Cards → grid of per-node buildCompact (expand ↗ opens the modal)
//       • DAG   → MultiToolDagGraph: a real topologically-layered flow graph
//                 (synthetic query root fanning out across LEVEL 1 / 2 / 3
//                 with dependency edges); click a node → the same modal.
//
// Per the Stage-D plan the page requests focused mode (collapse the workspaces
// sidebar + rail, re-openable via the shell's edge toggles) so the comparison
// grid gets the full viewport width.
// ============================================================================

import { useMemo, useState } from 'react';
import { Boxes, GitBranch, LayoutGrid } from 'lucide-react';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import { cn } from '@/utils/cn';
import { buildDagModel, type DagModel } from './dagModel';
import { DagHeaderStrip } from './DagHeaderStrip';
import { DagNodeBody } from './DagNodeBody';
import { MultiToolDagGraph } from './MultiToolDagGraph';
import { ExpandedViewProvider, useOpenExtendedView } from './expandedView';

type Props = {
  /** Raw value of the ``?context=`` URL param (URI-encoded JSON). */
  contextParam: string;
};

export function MultiToolDagCanvas({ contextParam }: Props) {
  const model = useMemo(() => buildDagModel(contextParam), [contextParam]);

  // The DAG page owns the full viewport width — collapse the workspaces
  // sidebar + copilot rail (re-openable via the shell's edge toggles).  Per
  // the Stage-D plan: PMs comparing multiple things often ask follow-ups, so
  // the rail stays collapsed-but-accessible rather than removed.
  useRequestFocusedMode(true);

  // Boundary case (rendering_density §3.4): a context that decoded to zero
  // renderable entries (only truly-unknown tools).  BuildShell's dispatch
  // gate (decodePrimitiveList length > 1) makes this unreachable in practice,
  // but render an honest affordance defensively.
  if (model.nodes.length === 0) {
    return (
      <div className="ambient-grid flex h-full min-h-0 flex-col items-center justify-center gap-2 px-6 text-center">
        <Boxes size={16} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
        <p className="text-[13px] text-fg-secondary">
          None of the tools in this answer can be rendered on Build yet.
        </p>
        <p className="text-[11.5px] text-fg-faint">
          Use Ask to explore them while we wire in their Build surfaces.
        </p>
      </div>
    );
  }

  return (
    <ExpandedViewProvider queryLabel={model.prompt}>
      <DagCanvasInner model={model} />
    </ExpandedViewProvider>
  );
}

type DagTab = 'cards' | 'dag';

function DagCanvasInner({
  model,
}: {
  model: DagModel;
}) {
  const { lastOpenedNodeId } = useOpenExtendedView();
  // Default tab is the compact cards — most analysts read the data, not the
  // topology.  The DAG (flow graph) is opt-in via the tab (per the user's
  // "don't put the DAG in their face" guidance).
  const [tab, setTab] = useState<DagTab>('cards');

  // Tabs only matter when there are ≥2 nodes (a real comparison / flow).
  const showTabs = model.nodes.length >= 2;

  // Card density: medium (≤3 nodes) → 3-up; small (≥4) → 4-up.  Mirrors the
  // ``size`` the model passes to each buildCompact.
  const gridCols =
    model.size === 'small'
      ? 'sm:grid-cols-2 lg:grid-cols-4'
      : 'sm:grid-cols-2 lg:grid-cols-3';

  const showDag = showTabs && tab === 'dag';

  return (
    <div className="ambient-grid flex h-full min-h-0 flex-col overflow-hidden">
      <DagHeaderStrip model={model} />
      {showTabs && <DagTabBar tab={tab} onChange={setTab} />}

      {showDag ? (
        <MultiToolDagGraph model={model} queryLabel={model.prompt} />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div
            className={cn('grid content-start gap-3 px-6 py-5', gridCols)}
          >
            {model.nodes.map((node) => (
              <div
                key={node.id}
                className={cn(
                  'rounded-[12px] transition-shadow duration-200',
                  node.id === lastOpenedNodeId &&
                    'ring-1 ring-ice-400/40 shadow-[0_0_0_3px_rgba(122,162,255,0.08)]',
                )}
              >
                <DagNodeBody node={node} size={model.size} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Tab bar — "Cards" (default) vs "DAG" (the flow graph).
// ----------------------------------------------------------------------------

function DagTabBar({
  tab,
  onChange,
}: {
  tab: DagTab;
  onChange: (t: DagTab) => void;
}) {
  // The flow archetype (parallel / multi-step) is stated once in the header
  // summary above — not repeated here.
  return (
    <div className="flex shrink-0 items-center gap-1 border-b border-line-subtle px-6 py-2">
      <TabButton
        active={tab === 'cards'}
        onClick={() => onChange('cards')}
        icon={<LayoutGrid size={12} strokeWidth={2} aria-hidden />}
        label="Cards"
      />
      <TabButton
        active={tab === 'dag'}
        onClick={() => onChange('dag')}
        icon={<GitBranch size={12} strokeWidth={2} aria-hidden />}
        label="DAG"
      />
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11.5px] font-medium transition-colors',
        active
          ? 'border-ice-400/40 bg-ice-400/[0.10] text-fg-primary'
          : 'border-transparent text-fg-secondary hover:bg-white/[0.04] hover:text-fg-primary',
      )}
    >
      {icon}
      {label}
    </button>
  );
}
