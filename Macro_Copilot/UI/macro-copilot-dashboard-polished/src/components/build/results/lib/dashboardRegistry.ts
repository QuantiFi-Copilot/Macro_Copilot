// ============================================================================
// dashboardRegistry.ts — workspace → specialised dashboard selector.
// ----------------------------------------------------------------------------
// PR7 — picks the right Results-tab dashboard for a persisted
// workspace.  The Results tab consumes ``resolveWorkflowDashboard``
// directly:
//
//   const kind = resolveWorkflowDashboard(workspace);
//   switch (kind) {
//     case 'event_study': return <EventStudyDashboard ... />
//     case 'regime_conditioned_relationship': return <RegimeRelationshipDashboard ... />
//     case 'backtest': return <BacktestDashboard ... />
//     case 'generic': return <GenericResultsDashboard ... />
//   }
//
// Discipline
// ----------
// 1. ``workspace.template_id`` is the AUTHORITATIVE signal.  PR-B
//    backend persistence stamps it on every forkable workspace so
//    we can route deterministically without fragile string-matching
//    on titles or node lists.
// 2. When ``template_id`` is null (legacy workspaces predating
//    PR-B), we fall back to a node-topology heuristic over the
//    canonical operator names each workflow uses.  This is
//    explicit + auditable; no title-string matching.
// 3. ``backtest`` is in ``PAUSED_WORKFLOWS`` from PR1 — we route to
//    the BacktestDashboard regardless of paused status because the
//    dashboard itself renders honest paused vs runnable copy.
// 4. Unknown template_ids → ``'generic'`` (fall back to the PR4
//    payload-backed artifact grid).
//
// Pure function — no React, no async — easy to test.  Adding a new
// dashboard kind is one entry on the union below + one branch in
// the consumer.
// ============================================================================

import type { WorkspaceDetail } from '@/services/workspaceApi';

/** Closed-vocabulary dashboard kind.  Adding a new specialised
 *  dashboard is a one-line union extension + a one-line consumer
 *  branch in ``ResultsView``. */
export type DashboardKind =
  | 'event_study'
  | 'regime_conditioned_relationship'
  | 'backtest'
  | 'generic';

/** Closed list of template IDs each specialised dashboard handles.
 *  Adding a new template that the dashboard understands = add the
 *  ID here.  Mirrors backend's template registry at PR review
 *  time.  Verified against
 *  ``rates_agent/workflows/{event_study,regime_conditioned_relationship,backtest}/template.yaml``. */
const TEMPLATE_TO_DASHBOARD: Record<string, DashboardKind> = {
  event_study: 'event_study',
  regime_conditioned_relationship: 'regime_conditioned_relationship',
  backtest: 'backtest',
};

/** Topology fingerprints — operator names that distinguish each
 *  workflow archetype.  Used ONLY as a fallback for legacy
 *  workspaces with null ``template_id``.  Each fingerprint is a set
 *  of operator names whose intersection with the workspace's
 *  operators uniquely identifies the workflow.  Audit against the
 *  template.yaml files at PR review time:
 *
 *    event_study                     — has ``threshold_events`` +
 *                                       ``event_windows`` +
 *                                       ``conditional_aggregate``.
 *    regime_conditioned_relationship — has ``rolling_regression`` +
 *                                       ``apply_mask`` +
 *                                       ``summarize_series``.
 *    backtest                        — has ``construct_trades`` +
 *                                       ``evaluate_trades`` +
 *                                       ``summarize_trades``.
 *
 *  Each fingerprint is checked in priority order; the first match
 *  wins.  Unknown topologies fall through to ``'generic'``. */
const TOPOLOGY_FINGERPRINTS: Array<{
  kind: DashboardKind;
  operators: ReadonlySet<string>;
}> = [
  {
    kind: 'backtest',
    operators: new Set([
      'construct_trades',
      'evaluate_trades',
      'summarize_trades',
    ]),
  },
  {
    kind: 'regime_conditioned_relationship',
    operators: new Set([
      'rolling_regression',
      'apply_mask',
      'summarize_series',
    ]),
  },
  {
    kind: 'event_study',
    operators: new Set([
      'threshold_events',
      'event_windows',
      'conditional_aggregate',
    ]),
  },
];

/** Resolve a workspace to a dashboard kind.  Pure; safe to call
 *  inside ``useMemo`` for re-render efficiency. */
export function resolveWorkflowDashboard(
  workspace: Pick<WorkspaceDetail, 'template_id' | 'nodes'>,
): DashboardKind {
  // 1. Template ID is authoritative.
  if (workspace.template_id && workspace.template_id in TEMPLATE_TO_DASHBOARD) {
    return TEMPLATE_TO_DASHBOARD[workspace.template_id];
  }

  // 2. Topology fingerprint fallback for legacy workspaces.
  const operatorNames = extractOperatorNames(workspace.nodes);
  for (const fp of TOPOLOGY_FINGERPRINTS) {
    if (isSupersetOf(operatorNames, fp.operators)) {
      return fp.kind;
    }
  }

  // 3. Truly unknown → generic.
  return 'generic';
}

/** True when ``set`` contains every member of ``required``. */
function isSupersetOf<T>(
  set: Set<T>,
  required: ReadonlySet<T>,
): boolean {
  for (const item of required) {
    if (!set.has(item)) return false;
  }
  return true;
}

/** Extract operator names from the workspace's node list.  Reads
 *  ``node.params.operator_name`` (the substrate's persisted shape)
 *  and returns the set.  Empty when no operators are present (pure
 *  primitive workspaces). */
function extractOperatorNames(
  nodes: WorkspaceDetail['nodes'],
): Set<string> {
  const out = new Set<string>();
  for (const n of nodes) {
    const params = (n.params ?? {}) as Record<string, unknown>;
    const opName = params['operator_name'];
    if (typeof opName === 'string' && opName.length > 0) {
      out.add(opName);
    }
  }
  return out;
}

/** Optional metadata about a dashboard kind — used by the Results
 *  tab to render a short caption beside the toggle.  Stable strings
 *  so tests can assert on them. */
export function describeDashboardKind(kind: DashboardKind): {
  label: string;
  summary: string;
} {
  switch (kind) {
    case 'event_study':
      return {
        label: 'Event study',
        summary:
          'Signal → events → forward windows → conditional vs unconditional aggregate.',
      };
    case 'regime_conditioned_relationship':
      return {
        label: 'Regime-conditioned relationship',
        summary:
          'Rolling relationship between two series, split by an external regime signal.',
      };
    case 'backtest':
      return {
        label: 'Backtest',
        summary:
          'Signal → trade construction → evaluation → summary.  Execution is paused on the LLM-facing surface today.',
      };
    case 'generic':
      return {
        label: 'Generic',
        summary:
          'No specialised dashboard for this workflow; payload-backed artifact cards below.',
      };
  }
}
