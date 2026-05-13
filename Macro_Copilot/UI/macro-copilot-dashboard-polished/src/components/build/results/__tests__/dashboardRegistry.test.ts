/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// dashboardRegistry.test.ts — PR7 dashboard selection + artifact resolver.
// ----------------------------------------------------------------------------
// Locks the load-bearing PR7 invariants:
//
//   1. ``resolveWorkflowDashboard`` selects the right specialised
//      dashboard from ``workspace.template_id`` (authoritative).
//   2. Topology-fingerprint fallback works when template_id is null
//      (legacy workspaces).
//   3. Unknown template_ids + unknown topologies → 'generic'.
//   4. ``resolveEventStudyArtifacts`` / ``resolveRegimeArtifacts`` /
//      ``resolveBacktestArtifacts`` map persisted nodes to the
//      canonical role keys for each workflow.
//   5. Missing-required-role detection works (resolver returns the
//      role keys whose binding came up null).
//   6. Unbound nodes are surfaced for the "Other artifacts" tail.
// ============================================================================

import {
  describeDashboardKind,
  resolveWorkflowDashboard,
} from '../lib/dashboardRegistry';
import {
  resolveBacktestArtifacts,
  resolveEventStudyArtifacts,
  resolveRegimeArtifacts,
} from '../lib/resolveWorkflowArtifacts';
import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';

type Check = { label: string; fn: () => void };
const _checks: Check[] = [];

function check(label: string, fn: () => void): void {
  _checks.push({ label, fn });
}

function assertEqual<T>(actual: T, expected: T, label: string): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    throw new Error(
      `assertEqual failed: ${label}\n  expected: ${e}\n  actual:   ${a}`,
    );
  }
}

function assertTruthy(value: unknown, label: string): void {
  if (!value) throw new Error(`assertTruthy failed: ${label}`);
}

function node(
  id: string,
  kind: 'primitive' | 'operator',
  operatorName?: string,
): NodeSummary {
  const params: Record<string, unknown> = {};
  if (operatorName) {
    params.operator_name = operatorName;
    params.params = {};
  } else {
    params.tool_name = `${id}_tool`;
    params.params = {};
  }
  return {
    node_id: id,
    kind,
    name: id,
    params,
    artifact_hash: 'a'.repeat(64),
    artifact: null,
  };
}

function workspace(args: {
  template_id?: string | null;
  nodes: NodeSummary[];
  focus?: string | null;
}): WorkspaceDetail {
  return {
    workspace_id: 'ws',
    slug: 'ws',
    name: null,
    dag_hash: 'd'.repeat(64),
    focus_node: args.focus ?? null,
    parent_workspace_id: null,
    schema_version: 1,
    created_by: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    nodes: args.nodes,
    edges: [],
    template_id: args.template_id ?? null,
    bound_slot_values: null,
  };
}

// ----------------------------------------------------------------------------
// Fixtures — match the canonical template.yaml node IDs
// ----------------------------------------------------------------------------

function eventStudyWorkspace(): WorkspaceDetail {
  return workspace({
    template_id: 'event_study',
    focus: 'compare',
    nodes: [
      node('signal', 'primitive'),
      node('target', 'primitive'),
      node('align', 'operator', 'align_series'),
      node('signal_aligned', 'operator', 'select_from_series_set'),
      node('target_aligned', 'operator', 'select_from_series_set'),
      node('events', 'operator', 'threshold_events'),
      node('windows', 'operator', 'event_windows'),
      node('aggregate', 'operator', 'conditional_aggregate'),
      node('unconditional_events', 'operator', 'threshold_events'),
      node('unconditional_windows', 'operator', 'event_windows'),
      node('unconditional_aggregate', 'operator', 'conditional_aggregate'),
      node('compare', 'operator', 'series_arithmetic'),
    ],
  });
}

function regimeWorkspace(): WorkspaceDetail {
  return workspace({
    template_id: 'regime_conditioned_relationship',
    focus: 'compare',
    nodes: [
      node('lhs', 'primitive'),
      node('rhs', 'primitive'),
      node('regime_signal', 'primitive'),
      node('relationship', 'operator', 'rolling_regression'),
      node('beta', 'operator', 'select_from_series_set'),
      node('regime_diff', 'operator', 'series_arithmetic'),
      node('high_mask', 'operator', 'threshold_events'),
      node('low_mask', 'operator', 'threshold_events'),
      node('high_betas', 'operator', 'apply_mask'),
      node('low_betas', 'operator', 'apply_mask'),
      node('high_summary', 'operator', 'summarize_series'),
      node('low_summary', 'operator', 'summarize_series'),
      node('compare', 'operator', 'series_arithmetic'),
    ],
  });
}

function backtestWorkspace(): WorkspaceDetail {
  return workspace({
    template_id: 'backtest',
    focus: 'summarize',
    nodes: [
      node('signal', 'primitive'),
      node('events', 'operator', 'threshold_events'),
      node('trades', 'operator', 'construct_trades'),
      node('price_panel', 'primitive'),
      node('financing', 'primitive'),
      node('evaluate', 'operator', 'evaluate_trades'),
      node('summarize', 'operator', 'summarize_trades'),
    ],
  });
}

// ----------------------------------------------------------------------------
// 1. Registry selection by template_id (authoritative)
// ----------------------------------------------------------------------------

check('registry: template_id="event_study" → event_study', () => {
  assertEqual(
    resolveWorkflowDashboard(eventStudyWorkspace()),
    'event_study',
    'kind',
  );
});

check('registry: template_id="regime_conditioned_relationship" → regime', () => {
  assertEqual(
    resolveWorkflowDashboard(regimeWorkspace()),
    'regime_conditioned_relationship',
    'kind',
  );
});

check('registry: template_id="backtest" → backtest', () => {
  assertEqual(
    resolveWorkflowDashboard(backtestWorkspace()),
    'backtest',
    'kind',
  );
});

check('registry: unknown template_id → generic', () => {
  assertEqual(
    resolveWorkflowDashboard(
      workspace({
        template_id: 'totally_made_up',
        nodes: [node('A', 'primitive')],
      }),
    ),
    'generic',
    'kind',
  );
});

// ----------------------------------------------------------------------------
// 2. Topology-fingerprint fallback when template_id is null
// ----------------------------------------------------------------------------

check('registry: null template_id + event_study fingerprint → event_study', () => {
  const ws = eventStudyWorkspace();
  ws.template_id = null;
  assertEqual(
    resolveWorkflowDashboard(ws),
    'event_study',
    'fingerprint fallback',
  );
});

check('registry: null template_id + regime fingerprint → regime', () => {
  const ws = regimeWorkspace();
  ws.template_id = null;
  assertEqual(
    resolveWorkflowDashboard(ws),
    'regime_conditioned_relationship',
    'fingerprint fallback',
  );
});

check('registry: null template_id + backtest fingerprint → backtest', () => {
  const ws = backtestWorkspace();
  ws.template_id = null;
  assertEqual(
    resolveWorkflowDashboard(ws),
    'backtest',
    'fingerprint fallback',
  );
});

check('registry: null template_id + no matching fingerprint → generic', () => {
  assertEqual(
    resolveWorkflowDashboard(
      workspace({
        template_id: null,
        nodes: [
          node('signal', 'primitive'),
          // No matching operators present.
        ],
      }),
    ),
    'generic',
    'no fingerprint match',
  );
});

check('registry: backtest fingerprint takes priority over event_study (shares threshold_events)', () => {
  // A workspace with construct_trades / evaluate_trades / summarize_trades
  // AND threshold_events / event_windows / conditional_aggregate should
  // be classified as backtest, not event_study, because the backtest
  // fingerprint is checked first.  Defensive priority.
  const ws = workspace({
    template_id: null,
    nodes: [
      node('signal', 'primitive'),
      node('events', 'operator', 'threshold_events'),
      node('windows', 'operator', 'event_windows'),
      node('aggregate', 'operator', 'conditional_aggregate'),
      node('trades', 'operator', 'construct_trades'),
      node('evaluate', 'operator', 'evaluate_trades'),
      node('summarize', 'operator', 'summarize_trades'),
    ],
  });
  assertEqual(resolveWorkflowDashboard(ws), 'backtest', 'priority');
});

// ----------------------------------------------------------------------------
// 3. describeDashboardKind: total over the union
// ----------------------------------------------------------------------------

check('describeDashboardKind: every kind has copy', () => {
  for (const k of ['event_study', 'regime_conditioned_relationship', 'backtest', 'generic'] as const) {
    const d = describeDashboardKind(k);
    assertTruthy(d.label.length > 0, `${k}: label`);
    assertTruthy(d.summary.length > 0, `${k}: summary`);
  }
});

// ----------------------------------------------------------------------------
// 4. resolveEventStudyArtifacts: role binding
// ----------------------------------------------------------------------------

check('resolveEventStudyArtifacts: binds every canonical node_id', () => {
  const m = resolveEventStudyArtifacts(eventStudyWorkspace());
  const r = m.roles;
  assertTruthy(r.signal, 'signal bound');
  assertTruthy(r.target, 'target bound');
  assertTruthy(r.align, 'align bound');
  assertTruthy(r.events, 'events bound');
  assertTruthy(r.windows, 'windows bound');
  assertTruthy(r.conditionalAggregate, 'aggregate bound');
  assertTruthy(r.unconditionalAggregate, 'unconditional_aggregate bound');
  assertTruthy(r.output, 'compare bound to output');
  assertEqual(r.output?.node_id, 'compare', 'output is compare');
  assertEqual(m.missingRequiredRoles, [], 'no missing roles');
});

check('resolveEventStudyArtifacts: missing nodes → null + reported as missing', () => {
  const ws = eventStudyWorkspace();
  // Strip out events + windows.
  ws.nodes = ws.nodes.filter(
    (n) => n.node_id !== 'events' && n.node_id !== 'windows',
  );
  const m = resolveEventStudyArtifacts(ws);
  assertEqual(m.roles.events, null, 'events null');
  assertEqual(m.roles.windows, null, 'windows null');
  const missing = m.missingRequiredRoles.sort();
  assertEqual(missing, ['events', 'windows'], 'both reported missing');
});

check('resolveEventStudyArtifacts: focus_node override wins for output', () => {
  // A workspace whose focus_node points at an unusual terminal
  // (template variant with a renamed terminal node) should bind
  // that node to ``output`` rather than the canonical "compare".
  const ws = eventStudyWorkspace();
  // Insert a custom terminal node.
  ws.nodes.push(node('custom_terminal', 'operator', 'series_arithmetic'));
  ws.focus_node = 'custom_terminal';
  const m = resolveEventStudyArtifacts(ws);
  assertEqual(m.roles.output?.node_id, 'custom_terminal', 'focus wins');
});

check('resolveEventStudyArtifacts: surfaces unboundNodes', () => {
  const ws = eventStudyWorkspace();
  ws.nodes.push(node('extra_node', 'operator', 'series_arithmetic'));
  const m = resolveEventStudyArtifacts(ws);
  const unboundIds = m.unboundNodes.map((n) => n.node_id);
  assertTruthy(unboundIds.includes('extra_node'), 'extra is unbound');
  // signal_aligned / target_aligned / unconditional_events /
  // unconditional_windows are also unbound (the event-study role
  // map intentionally only covers the load-bearing nodes).
  assertTruthy(unboundIds.length >= 5, 'multiple unbound nodes');
});

// ----------------------------------------------------------------------------
// 5. resolveRegimeArtifacts: role binding
// ----------------------------------------------------------------------------

check('resolveRegimeArtifacts: binds every canonical node_id', () => {
  const m = resolveRegimeArtifacts(regimeWorkspace());
  const r = m.roles;
  assertTruthy(r.lhs, 'lhs');
  assertTruthy(r.rhs, 'rhs');
  assertTruthy(r.regimeSignal, 'regime_signal');
  assertTruthy(r.relationship, 'relationship');
  assertTruthy(r.beta, 'beta');
  assertTruthy(r.highMask, 'high_mask');
  assertTruthy(r.lowMask, 'low_mask');
  assertTruthy(r.highSummary, 'high_summary');
  assertTruthy(r.lowSummary, 'low_summary');
  assertEqual(r.output?.node_id, 'compare', 'output is compare');
  assertEqual(m.missingRequiredRoles, [], 'no missing required roles');
});

check('resolveRegimeArtifacts: missing high_summary → reported absent', () => {
  const ws = regimeWorkspace();
  ws.nodes = ws.nodes.filter((n) => n.node_id !== 'high_summary');
  const m = resolveRegimeArtifacts(ws);
  assertEqual(m.roles.highSummary, null, 'high_summary null');
});

// ----------------------------------------------------------------------------
// 6. resolveBacktestArtifacts: role binding
// ----------------------------------------------------------------------------

check('resolveBacktestArtifacts: binds canonical backtest node_ids', () => {
  const m = resolveBacktestArtifacts(backtestWorkspace());
  const r = m.roles;
  assertTruthy(r.signal, 'signal');
  assertTruthy(r.events, 'events');
  assertTruthy(r.trades, 'trades');
  assertTruthy(r.pricePanel, 'price_panel');
  assertTruthy(r.financing, 'financing');
  assertTruthy(r.evaluate, 'evaluate');
  assertTruthy(r.summarize, 'summarize');
  assertEqual(m.missingRequiredRoles, [], 'no missing');
});

check('resolveBacktestArtifacts: paused workspace (no execution stages) → trades/eval/summary null', () => {
  // Simulate a backtest workspace where only the setup persisted
  // (the execution side was paused).  All three execution roles
  // should be reported missing.
  const ws = backtestWorkspace();
  ws.nodes = ws.nodes.filter(
    (n) => !['trades', 'evaluate', 'summarize'].includes(n.node_id),
  );
  const m = resolveBacktestArtifacts(ws);
  assertEqual(m.roles.trades, null, 'no trades');
  assertEqual(m.roles.evaluate, null, 'no evaluate');
  assertEqual(m.roles.summarize, null, 'no summarize');
  const missing = m.missingRequiredRoles.sort();
  assertEqual(
    missing,
    ['summarize', 'trades'],
    'reported missing (signal+events present)',
  );
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export function runAllDashboardRegistryTests(): void {
  let passed = 0;
  let failed = 0;
  for (const { label, fn } of _checks) {
    try {
      fn();
      passed += 1;
    } catch (err) {
      failed += 1;
      // eslint-disable-next-line no-console
      console.error(`✗ ${label}\n  ${(err as Error).message}`);
    }
  }
  // eslint-disable-next-line no-console
  console.log(
    `\ndashboard-registry coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} dashboard-registry check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllDashboardRegistryTests();
}
