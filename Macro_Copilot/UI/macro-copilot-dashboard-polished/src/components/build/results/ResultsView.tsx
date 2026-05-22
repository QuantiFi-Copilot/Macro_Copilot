// ============================================================================
// ResultsView — Results-tab orchestration.
// ----------------------------------------------------------------------------
// PR7 — picks the right specialised dashboard for this workspace
// via ``resolveWorkflowDashboard`` and renders it.  Unsupported
// workflows fall through to ``GenericResultsDashboard`` (the
// pre-PR7 grid).  Specialised dashboards expose an "All artifacts"
// toggle below the canvas so the user can still inspect every
// persisted artifact without leaving the tab.
//
// Discipline
// ----------
// 1. Dashboard selection is data-driven (``workspace.template_id``
//    + topology fingerprint fallback).  No string-matching on
//    titles.
// 2. Generic grid is the default.  Adding a new specialised
//    dashboard = one registry entry + one branch here + one new
//    component file; no other call sites change.
// 3. Specialised dashboards consume the existing PR4 payload-
//    backed widgets via ``NodeWidgetCard``; they never compute
//    metrics client-side.
// ============================================================================

import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import {
  describeDashboardKind,
  resolveWorkflowDashboard,
} from './lib/dashboardRegistry';
import { GenericResultsDashboard } from './dashboards/GenericResultsDashboard';
import {
  EventStudyDashboard,
  EventStudyAllArtifactsFallback,
} from './dashboards/EventStudyDashboard';
import {
  RegimeRelationshipDashboard,
  RegimeAllArtifactsFallback,
} from './dashboards/RegimeRelationshipDashboard';
import {
  BacktestDashboard,
  BacktestAllArtifactsFallback,
} from './dashboards/BacktestDashboard';

type Props = {
  detail: WorkspaceDetail;
};

export function ResultsView({ detail }: Props) {
  const kind = useMemo(() => resolveWorkflowDashboard(detail), [detail]);

  if (detail.nodes.length === 0) {
    return <EmptyResults />;
  }

  if (kind === 'generic') {
    return (
      <div className="px-6 py-6">
        <GenericResultsDashboard detail={detail} />
      </div>
    );
  }

  return <SpecialisedShell detail={detail} kind={kind} />;
}

// ----------------------------------------------------------------------------
// Specialised-dashboard shell with an "All artifacts" toggle below.
// ----------------------------------------------------------------------------

function SpecialisedShell({
  detail,
  kind,
}: {
  detail: WorkspaceDetail;
  kind: 'event_study' | 'regime_conditioned_relationship' | 'backtest';
}) {
  const [showAll, setShowAll] = useState(false);
  const description = describeDashboardKind(kind);

  return (
    <div className="flex min-w-0 flex-col">
      {kind === 'event_study' && <EventStudyDashboard detail={detail} />}
      {kind === 'regime_conditioned_relationship' && (
        <RegimeRelationshipDashboard detail={detail} />
      )}
      {kind === 'backtest' && <BacktestDashboard detail={detail} />}

      <div className="border-t border-line-subtle px-6 py-4">
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="flex items-center gap-1.5 rounded-md border border-line-soft bg-white/[0.025] px-2.5 py-1 text-[11px] font-medium text-fg-secondary transition-colors hover:border-ice-400/35 hover:text-ice-200"
          aria-expanded={showAll}
        >
          {showAll ? (
            <ChevronDown size={11} strokeWidth={1.75} aria-hidden />
          ) : (
            <ChevronRight size={11} strokeWidth={1.75} aria-hidden />
          )}
          <span>
            {showAll
              ? 'Hide all artifacts'
              : `Show all artifacts (${detail.nodes.length} total)`}
          </span>
        </button>
        <p className="mt-2 text-[10.5px] leading-[1.5] text-fg-faint">
          Specialised view above · {description.summary}
        </p>
        {showAll && (
          <div className="mt-4">
            {kind === 'event_study' && (
              <EventStudyAllArtifactsFallback detail={detail} />
            )}
            {kind === 'regime_conditioned_relationship' && (
              <RegimeAllArtifactsFallback detail={detail} />
            )}
            {kind === 'backtest' && (
              <BacktestAllArtifactsFallback detail={detail} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyResults() {
  return (
    <div className="px-6 py-10">
      <div className="card flex h-[200px] items-center justify-center text-[12px] text-fg-muted">
        No results to display — this workspace has no nodes.
      </div>
    </div>
  );
}
