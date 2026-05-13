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
import { BuildHeader } from './BuildHeader';
import { BuildTabs, type BuildTabId } from './BuildTabs';
import { DagView } from '../dag/DagView';
import { ResultsView } from '../results/ResultsView';
import { ParametersPlaceholder } from '../parameters/ParametersPlaceholder';
import { NotesPlaceholder } from '../notes/NotesPlaceholder';

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
    <div className="flex h-full min-h-0 flex-col">
      <BuildHeader detail={detail} status={status} />
      <BuildTabs active={active} onSelect={setActive} />

      <div className="min-h-0 flex-1 overflow-y-auto">
        {active === 'dag' && <DagView detail={detail} />}
        {active === 'results' && <ResultsView detail={detail} />}
        {active === 'parameters' && <ParametersPlaceholder />}
        {active === 'notes' && <NotesPlaceholder />}
      </div>
    </div>
  );
}
