// ============================================================================
// BuildCompleted — the Build canvas when a workspace is open + finished.
// ----------------------------------------------------------------------------
// Top: BuildHeader (title + actions).
// Middle: BuildTabs.
// Bottom: tab-routed content — DAG, Results, Parameters (PR B),
//         Notes (PR B).
//
// The active tab is local state so navigating between DAG / Results
// doesn't push a history entry; the URL only changes when the
// workspace slug changes.  ``initialTab`` lets a parent deep-link
// to "Results" from the chat's "Open in Build" handoff.
// ============================================================================

import { useState } from 'react';
import type { WorkspaceDetail, WorkspaceReplay } from '@/services/workspaceApi';
import { ExpandedViewProvider } from '../multitool/expandedView';
import { BuildHeader } from './BuildHeader';
import { BuildTabs, type BuildTabId } from './BuildTabs';
import { DagView } from '../dag/DagView';
import { ResultsView } from '../results/ResultsView';
import { ParametersView } from '../parameters/ParametersView';
import { NotesView } from '../notes/NotesView';
import { VariantStrip } from '../variants/VariantStrip';

type Props = {
  detail: WorkspaceDetail;
  /** ``replayWorkspace`` snapshot for the drift indicator.  Optional
   *  — when fetch fails, the status pill falls back to "Completed"
   *  unconditionally. */
  replay: WorkspaceReplay | null;
  initialTab?: BuildTabId;
};

export function BuildCompleted({
  detail,
  replay,
  initialTab = 'results',
}: Props) {
  const [active, setActive] = useState<BuildTabId>(initialTab);

  // Status pill derivation: methodology drift flips us to "paused";
  // otherwise we always read as "completed" — PR B will add an
  // "editing" status when the user starts overriding parameters.
  const status: 'completed' | 'editing' | 'paused' =
    replay && replay.methodology_diffs.length > 0
      ? 'paused'
      : 'completed';

  return (
    // Consolidation target #2 — the persisted slug page hosts the SAME
    // expand-to-modal provider the live multi-tool DAG uses, so every
    // primitive node card can open its module's buildExtended.  The
    // contextNote is the P4/P5 honesty banner: the modal is a LIVE
    // re-query; the saved cards stay frozen, read-only by hash.
    <ExpandedViewProvider
      queryLabel={detail.name ?? detail.slug}
      contextNote="Fetches today's data with this node's saved parameters — the saved workspace below stays frozen (read-only by artifact hash)."
    >
    <div className="flex h-full min-h-0 flex-col">
      <BuildHeader detail={detail} status={status} />
      <VariantStrip detail={detail} />
      <BuildTabs active={active} onSelect={setActive} />

      <div className="min-h-0 flex-1 overflow-hidden">
        {active === 'dag' && (
          <div className="h-full overflow-y-auto">
            <DagView detail={detail} />
          </div>
        )}
        {active === 'results' && (
          <div className="h-full overflow-y-auto">
            <ResultsView detail={detail} />
          </div>
        )}
        {active === 'parameters' && <ParametersView detail={detail} />}
        {active === 'notes' && <NotesView detail={detail} />}
      </div>
    </div>
    </ExpandedViewProvider>
  );
}
