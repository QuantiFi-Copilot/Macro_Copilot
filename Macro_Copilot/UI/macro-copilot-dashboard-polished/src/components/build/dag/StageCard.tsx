// ============================================================================
// StageCard — one node card in the DAG view.
// ----------------------------------------------------------------------------
// Visual unit of Mockup B/C's DAG row.  Reuses the ``research-card``
// surface treatment + the gradient top-rule keyed to stage category
// (input → ice, transform → violet, output → amber) so a stage card
// reads identically to a Monitor widget in colour register.  Layout:
//
//   ┌─────────────────────────────────────┐
//   │ ① <Title>                       ↗   │  (badge + title + arrow)
//   │   Primitive                         │  (kind sub-label, kicker)
//   │ ─────────────────────────────────── │
//   │ Param 1     value                   │
//   │ Param 2     value                   │
//   │ Param 3     value                   │
//   │ ─────────────────────────────────── │
//   │ ● ready · <type>      <lineage>     │  (status footer)
//   └─────────────────────────────────────┘
//
// Cards are click-targets in PR B (opens the Parameters tab pre-
// focused on this stage).  PR A renders the click as a no-op
// to keep the visual intact; the cursor stays default to signal
// "look, don't tap".
// ============================================================================

import { ArrowUpRight, Loader2 } from 'lucide-react';
import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';
import { useOpenExtendedViewOptional } from '@/components/build/multitool/expandedView';
import {
  canExpandPersistedNode,
  decodedForPersistedNode,
} from '@/components/build/lib/persistedExpand';
import {
  prettyStageTitle,
  stageKindLabel,
} from '@/components/build/lib/stageDisplay';
import {
  railColorForStage,
  stageCategoryForNode,
} from '@/components/build/lib/stageCategory';
import { cn } from '@/utils/cn';
import { StageBadge } from './StageBadge';
import { StageParamsList } from './StageParamsList';

type Props = {
  node: NodeSummary;
  workspace: WorkspaceDetail;
  /** 1-indexed position within the DAG view's ordering.  Drives
   *  the StageBadge content. */
  index: number;
  /** When true, render this card as the focus / terminal — adds a
   *  slightly heavier ring to anchor the user's eye on the final
   *  output node. */
  isTerminal?: boolean;
};

export function StageCard({ node, workspace, index, isTerminal = false }: Props) {
  const category = stageCategoryForNode(node, workspace);
  const railColor = railColorForStage(category);
  const title = prettyStageTitle(node.name ?? node.node_id);
  const kind = stageKindLabel(node.kind);

  // Consolidation target #2 — primitive stage cards gain the SAME
  // expand→buildExtended affordance the live multi-tool DAG has
  // (tolerant outside an ExpandedViewProvider: the arrow stays the
  // original decorative glyph).  Operator nodes keep the decorative
  // arrow — no owning module to expand into.
  const expandCtx = useOpenExtendedViewOptional();
  const canExpand = expandCtx != null && canExpandPersistedNode(node);
  const handleExpand = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!expandCtx) return;
    const decoded = decodedForPersistedNode(node);
    if (decoded) expandCtx.open(decoded, -1);
  };
  const hasArtifact = node.artifact_hash != null;
  const artifactType = node.artifact?.artifact_type ?? null;
  const shortHash = node.artifact_hash
    ? shortenHash(node.artifact_hash)
    : null;

  return (
    <article
      className={cn(
        'research-card group relative flex min-w-[228px] max-w-[260px] flex-col overflow-hidden',
        isTerminal && 'ring-1 ring-amber-400/25 ring-offset-1 ring-offset-transparent',
      )}
      style={{ ['--rail-color' as string]: railColor }}
    >
      <span aria-hidden className="research-card-rail" />

      <div className="flex items-start gap-2.5 px-4 pt-3 pb-1.5">
        <StageBadge number={index} category={category} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <h4 className="truncate text-[12.5px] font-semibold tracking-[-0.008em] text-fg-primary">
              {title}
            </h4>
            {canExpand ? (
              <button
                type="button"
                onClick={handleExpand}
                aria-label="Open the live extended view of this tool"
                title="Open live extended view (saved card stays frozen)"
                className="mt-0.5 shrink-0 rounded text-fg-faint transition-colors hover:text-ice-200"
              >
                <ArrowUpRight size={12} strokeWidth={1.75} aria-hidden />
              </button>
            ) : (
              <ArrowUpRight
                size={12}
                strokeWidth={1.75}
                aria-hidden
                className="mt-0.5 shrink-0 text-fg-faint transition-colors group-hover:text-ice-200"
              />
            )}
          </div>
          <div className="mt-0.5 font-mono text-[9.5px] font-medium uppercase tracking-[0.18em] text-fg-faint">
            {kind}
          </div>
        </div>
      </div>

      <div className="research-card-divider mx-3" />

      <StageParamsList node={node} />

      <div className="research-card-divider mx-3" />

      <StageStatusFooter
        ready={hasArtifact}
        artifactType={artifactType}
        shortHash={shortHash}
      />
    </article>
  );
}

function StageStatusFooter({
  ready,
  artifactType,
  shortHash,
}: {
  ready: boolean;
  artifactType: string | null;
  shortHash: string | null;
}) {
  return (
    <div className="flex items-center justify-between gap-2 px-4 pb-3 pt-2 text-[10px]">
      <div className="flex min-w-0 items-center gap-1.5">
        {ready ? (
          <span
            aria-hidden
            className="inline-flex h-1.5 w-1.5 shrink-0 rounded-full bg-mint-400 shadow-[0_0_6px_rgba(63,214,154,0.6)]"
          />
        ) : (
          <Loader2
            size={10}
            strokeWidth={2}
            className="shrink-0 animate-spin text-amber-300"
            aria-hidden
          />
        )}
        <span className="truncate font-mono uppercase tracking-[0.14em] text-fg-faint">
          {ready ? 'ready' : 'pending'}
          {artifactType && (
            <span className="text-fg-muted/70"> · {artifactType}</span>
          )}
        </span>
      </div>
      {shortHash && (
        <span className="lineage-chip shrink-0" title={`lineage ${shortHash}`}>
          {shortHash}
        </span>
      )}
    </div>
  );
}

/** Trim an artifact hash to the conventional 8-char lineage prefix.
 *  Defensive: if the upstream emits an unexpectedly short hash we
 *  return it verbatim instead of slicing into nonsense. */
function shortenHash(hash: string): string {
  const cleaned = hash.replace(/^0x/, '');
  return cleaned.length <= 8 ? cleaned : cleaned.slice(0, 8);
}
