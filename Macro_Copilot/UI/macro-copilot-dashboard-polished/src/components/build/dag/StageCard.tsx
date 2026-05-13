// ============================================================================
// StageCard — one node card in the DAG view.
// ----------------------------------------------------------------------------
// Visual unit of Mockup B/C's DAG row.  Reuses the ``research-card``
// surface treatment + the gradient top-rule keyed to stage category
// (input → ice, transform → violet, output → amber) so a stage card
// reads identically to a Monitor widget in colour register.  Compact
// vertical layout:
//
//   ┌─────────────────────────────────────┐
//   │ ① PRIMITIVE / OPERATOR · kind label │
//   │   <tool / operator title>          ‹›│
//   │ ─────────────────────────────────── │
//   │ Param 1     value                   │
//   │ Param 2     value                   │
//   │ Param 3     value                   │
//   └─────────────────────────────────────┘
//
// Cards are click-targets in PR B (opens the Parameters tab pre-
// focused on this stage).  PR A renders the click as a no-op
// to keep the visual intact; the cursor stays default to signal
// "look, don't tap".
// ============================================================================

import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';
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

  return (
    <article
      className={cn(
        'research-card relative flex min-w-[220px] max-w-[260px] flex-col overflow-hidden',
        isTerminal && 'ring-1 ring-amber-400/25 ring-offset-1 ring-offset-transparent',
      )}
      style={{ ['--rail-color' as string]: railColor }}
    >
      <span aria-hidden className="research-card-rail" />

      <div className="flex items-start gap-2 px-4 pt-3 pb-1.5">
        <StageBadge number={index} category={category} />
        <div className="min-w-0">
          <div className="text-[9.5px] font-semibold uppercase tracking-[0.18em] text-fg-faint">
            {kind}
          </div>
          <h4 className="mt-0.5 truncate text-[12.5px] font-semibold tracking-[-0.008em] text-fg-primary">
            {title}
          </h4>
        </div>
      </div>

      <div className="research-card-divider mx-3" />

      <StageParamsList node={node} />
    </article>
  );
}
