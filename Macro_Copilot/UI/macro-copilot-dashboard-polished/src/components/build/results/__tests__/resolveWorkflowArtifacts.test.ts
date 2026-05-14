/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// resolveWorkflowArtifacts.test.ts — PR2 (new plan) resolver hardening.
// ----------------------------------------------------------------------------
// PR2 expands ``resolveBy`` from a node-id-only lookup to a three-layer
// walk: canonical node IDs → operator_name fallback → tool_name fallback.
// This file locks the new behavior:
//
//   - Layer 1 (nodeIds) still matches the canonical templates.
//   - Layer 2 (operatorNames) resolves a role when a workspace's node
//     ID has drifted from the canonical name but the operator_name is
//     intact (per the substrate's OperatorNode invariant).
//   - Layer 3 (toolNames) resolves a primitive role when the tool_name
//     identifies the slot even though the node ID was renamed.
//   - Each node may fill at most ONE role per run.  The pre-PR2
//     resolver would happily bind a single ``summarize_series`` node
//     to both ``highSummary`` and ``lowSummary`` — that bug is locked
//     out here.
//   - ``focus_node`` precedence for ``output`` still works AND never
//     steals a node already bound by another role.
//   - Missing-required-roles list is accurate.
//   - Unbound nodes are returned so the dashboard can render
//     "Other artifacts" beneath the specialised layout.
//
// Style mirrors widgetFormat.test.ts — local ``check`` shim + esbuild
// bundling, no test framework dependency.
// ============================================================================

import {
  resolveBacktestArtifacts,
  resolveEventStudyArtifacts,
  resolveRegimeArtifacts,
} from '../lib/resolveWorkflowArtifacts';
import type {
  NodeSummary,
  WorkspaceDetail,
} from '@/services/workspaceApi';

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

function assertTruthy(v: unknown, label: string): void {
  if (!v) throw new Error(`assertTruthy failed: ${label}`);
}

function assertFalsy(v: unknown, label: string): void {
  if (v) throw new Error(`assertFalsy failed: ${label}`);
}

// ---------------------------------------------------------------------------
// Fixture helpers — synthesize a minimal workspace shape the resolver
// can consume.  Only ``node_id``, ``kind``, ``name``, ``params`` are
// load-bearing for resolution; ``artifact_hash`` + ``artifact`` are
// passed through untouched.
// ---------------------------------------------------------------------------

function node(opts: {
  id: string;
  kind?: string;
  name?: string;
  operator?: string;
  tool?: string;
}): NodeSummary {
  const params: Record<string, unknown> = {};
  if (opts.operator) params.operator_name = opts.operator;
  if (opts.tool) params.tool_name = opts.tool;
  return {
    node_id: opts.id,
    kind: opts.kind ?? 'OperatorNode',
    name: opts.name ?? opts.id,
    params,
    artifact_hash: `h:${opts.id}`,
    artifact: null,
  };
}

function ws(
  nodes: NodeSummary[],
  focus: string | null = null,
): Pick<WorkspaceDetail, 'nodes' | 'focus_node'> {
  return { nodes, focus_node: focus };
}

// ---------------------------------------------------------------------------
// Layer 1 — canonical node IDs (regression: still works post-PR2).
// ---------------------------------------------------------------------------

check('event_study: canonical node IDs resolve every role', () => {
  const w = ws([
    node({ id: 'signal', kind: 'PrimitiveNode' }),
    node({ id: 'target', kind: 'PrimitiveNode' }),
    node({ id: 'events', operator: 'threshold_events' }),
    node({ id: 'windows', operator: 'event_windows' }),
    node({ id: 'aggregate', operator: 'conditional_aggregate' }),
    node({
      id: 'unconditional_aggregate',
      operator: 'rolling_unconditional_mean',
    }),
    node({ id: 'compare', operator: 'series_arithmetic' }),
  ]);
  const r = resolveEventStudyArtifacts(w);
  assertEqual(r.roles.signal?.node_id, 'signal', 'signal');
  assertEqual(r.roles.target?.node_id, 'target', 'target');
  assertEqual(r.roles.events?.node_id, 'events', 'events');
  assertEqual(r.roles.windows?.node_id, 'windows', 'windows');
  assertEqual(
    r.roles.conditionalAggregate?.node_id,
    'aggregate',
    'conditional_aggregate',
  );
  assertEqual(
    r.roles.unconditionalAggregate?.node_id,
    'unconditional_aggregate',
    'unconditional',
  );
  assertEqual(r.roles.output?.node_id, 'compare', 'output');
  assertEqual(r.missingRequiredRoles, [], 'no missing');
});

// ---------------------------------------------------------------------------
// Layer 2 — operator_name fallback.
// ---------------------------------------------------------------------------

check(
  'event_study: operator_name fallback resolves when node_id drifts',
  () => {
    // Same DAG, but every operator node has a renamed node_id.  The
    // resolver should fall back to operator_name and still bind the
    // right role.
    const w = ws([
      node({ id: 'signal', kind: 'PrimitiveNode' }),
      node({ id: 'target', kind: 'PrimitiveNode' }),
      node({ id: 'cpi_threshold', operator: 'threshold_events' }),
      node({ id: 'macro_windows', operator: 'event_windows' }),
      node({ id: 'mean_response', operator: 'conditional_aggregate' }),
      node({ id: 'spread', operator: 'series_arithmetic' }),
    ]);
    const r = resolveEventStudyArtifacts(w);
    assertEqual(
      r.roles.events?.node_id,
      'cpi_threshold',
      'events fallback',
    );
    assertEqual(
      r.roles.windows?.node_id,
      'macro_windows',
      'windows fallback',
    );
    assertEqual(
      r.roles.conditionalAggregate?.node_id,
      'mean_response',
      'agg fallback',
    );
    assertEqual(r.roles.output?.node_id, 'spread', 'output fallback');
  },
);

check(
  'regime: operator_name fallback distinguishes high vs low summaries',
  () => {
    // BOTH high_summary and low_summary use operator summarize_series.
    // The resolver must still produce DIFFERENT nodes (the canonical
    // IDs differentiate, and once a node is bound it cannot be picked
    // again).  The pre-PR2 implementation walked operator_name and
    // would have bound the same first match to both.
    const w = ws([
      node({ id: 'lhs', kind: 'PrimitiveNode' }),
      node({ id: 'rhs', kind: 'PrimitiveNode' }),
      node({ id: 'regime_signal', kind: 'PrimitiveNode' }),
      node({ id: 'rolling', operator: 'rolling_regression' }),
      node({ id: 'pick_beta', operator: 'select_from_series_set' }),
      node({ id: 'high_mask', operator: 'threshold_events' }),
      node({ id: 'low_mask', operator: 'threshold_events' }),
      // Both summary nodes share operator_name — resolved by node_id.
      node({ id: 'high_summary', operator: 'summarize_series' }),
      node({ id: 'low_summary', operator: 'summarize_series' }),
      node({ id: 'compare', operator: 'series_arithmetic' }),
    ]);
    const r = resolveRegimeArtifacts(w);
    assertEqual(
      r.roles.highSummary?.node_id,
      'high_summary',
      'high pinned',
    );
    assertEqual(
      r.roles.lowSummary?.node_id,
      'low_summary',
      'low pinned',
    );
    assertTruthy(
      r.roles.highSummary !== r.roles.lowSummary,
      'distinct nodes',
    );
  },
);

check(
  'regime: operator_name fallback never double-binds same node',
  () => {
    // The renamed summary nodes both share operator_name; only the
    // FIRST role's resolution can claim the first match, the second
    // role must fall through.  Because both candidates have the
    // same operator and we walk in declaration order, the binding
    // is deterministic.
    const w = ws([
      node({ id: 'lhs', kind: 'PrimitiveNode' }),
      node({ id: 'rhs', kind: 'PrimitiveNode' }),
      node({ id: 'regime_signal', kind: 'PrimitiveNode' }),
      node({ id: 'rolling', operator: 'rolling_regression' }),
      node({ id: 'pick_beta', operator: 'select_from_series_set' }),
      node({ id: 'high_mask', operator: 'threshold_events' }),
      node({ id: 'low_mask', operator: 'threshold_events' }),
      // Both renamed → no canonical node-id match for either.
      node({ id: 'summarize_high_band', operator: 'summarize_series' }),
      node({ id: 'summarize_low_band', operator: 'summarize_series' }),
      node({ id: 'compare', operator: 'series_arithmetic' }),
    ]);
    const r = resolveRegimeArtifacts(w);
    // highSummary should get the first declaration-order match;
    // lowSummary should NOT get the same node back.
    assertTruthy(r.roles.highSummary, 'high bound');
    assertTruthy(r.roles.lowSummary, 'low bound');
    assertTruthy(
      r.roles.highSummary?.node_id !== r.roles.lowSummary?.node_id,
      'distinct nodes (no double-bind)',
    );
  },
);

// ---------------------------------------------------------------------------
// Layer 3 — tool_name fallback.
// ---------------------------------------------------------------------------

check(
  'backtest: tool_name fallback resolves renamed primitive nodes',
  () => {
    // The backtest template wires the price panel + financing panel
    // through primitive nodes whose IDs are slot-substituted.  The
    // tool_name layer pins the role even when the ID drifts.
    const w = ws([
      node({ id: 'signal', kind: 'PrimitiveNode' }),
      node({ id: 'fomc_events', operator: 'threshold_events' }),
      node({ id: 'trade_set', operator: 'construct_trades' }),
      // Renamed primitive nodes — the tool_name is the load-bearing
      // identifier.
      node({
        id: 'sov_yields_node',
        kind: 'PrimitiveNode',
        tool: 'build_sovereign_yield_panel_tool',
      }),
      node({
        id: 'overnight_financing',
        kind: 'PrimitiveNode',
        tool: 'compute_financing_rate_tool',
      }),
      node({ id: 'pnl', operator: 'evaluate_trades' }),
      node({ id: 'wrap', operator: 'summarize_trades' }),
    ]);
    const r = resolveBacktestArtifacts(w);
    assertEqual(
      r.roles.pricePanel?.node_id,
      'sov_yields_node',
      'price panel via tool_name',
    );
    assertEqual(
      r.roles.financing?.node_id,
      'overnight_financing',
      'financing via tool_name',
    );
    assertEqual(
      r.roles.evaluate?.node_id,
      'pnl',
      'evaluate via operator_name',
    );
    assertEqual(
      r.roles.summarize?.node_id,
      'wrap',
      'summarize via operator_name',
    );
  },
);

// ---------------------------------------------------------------------------
// Layer precedence — node_id wins over operator_name when both match.
// ---------------------------------------------------------------------------

check(
  'event_study: canonical node_id beats operator_name fallback',
  () => {
    // Both ``events`` (canonical) and ``cpi_threshold`` (renamed with
    // same operator) exist — the canonical name MUST win, leaving the
    // renamed node available for the unbound-nodes list.
    const w = ws([
      node({ id: 'signal', kind: 'PrimitiveNode' }),
      node({ id: 'target', kind: 'PrimitiveNode' }),
      node({ id: 'events', operator: 'threshold_events' }),
      node({ id: 'cpi_threshold', operator: 'threshold_events' }),
      node({ id: 'windows', operator: 'event_windows' }),
      node({ id: 'aggregate', operator: 'conditional_aggregate' }),
      node({ id: 'compare', operator: 'series_arithmetic' }),
    ]);
    const r = resolveEventStudyArtifacts(w);
    assertEqual(r.roles.events?.node_id, 'events', 'canonical wins');
    const ub = r.unboundNodes.map((n) => n.node_id);
    assertTruthy(ub.includes('cpi_threshold'), 'extra node unbound');
  },
);

// ---------------------------------------------------------------------------
// focus_node precedence for ``output`` — still works, and never
// double-binds.
// ---------------------------------------------------------------------------

check(
  'event_study: focus_node overrides canonical output when both present',
  () => {
    const w = ws(
      [
        node({ id: 'signal', kind: 'PrimitiveNode' }),
        node({ id: 'target', kind: 'PrimitiveNode' }),
        node({ id: 'events', operator: 'threshold_events' }),
        node({ id: 'windows', operator: 'event_windows' }),
        node({ id: 'aggregate', operator: 'conditional_aggregate' }),
        node({ id: 'compare', operator: 'series_arithmetic' }),
        node({ id: 'custom_terminal', operator: 'series_arithmetic' }),
      ],
      'custom_terminal',
    );
    const r = resolveEventStudyArtifacts(w);
    assertEqual(
      r.roles.output?.node_id,
      'custom_terminal',
      'focus_node wins',
    );
    // The canonical ``compare`` node should now be unbound.
    const ub = r.unboundNodes.map((n) => n.node_id);
    assertTruthy(ub.includes('compare'), 'canonical falls into unbound');
  },
);

check('event_study: focus_node ignored when already bound elsewhere', () => {
  // If focus_node points at ``signal`` (which is bound to the signal
  // role first), output must NOT steal it back.
  const w = ws(
    [
      node({ id: 'signal', kind: 'PrimitiveNode' }),
      node({ id: 'target', kind: 'PrimitiveNode' }),
      node({ id: 'events', operator: 'threshold_events' }),
      node({ id: 'windows', operator: 'event_windows' }),
      node({ id: 'aggregate', operator: 'conditional_aggregate' }),
      node({ id: 'compare', operator: 'series_arithmetic' }),
    ],
    'signal',
  );
  const r = resolveEventStudyArtifacts(w);
  assertEqual(r.roles.signal?.node_id, 'signal', 'signal still bound');
  assertEqual(
    r.roles.output?.node_id,
    'compare',
    'output falls back to canonical',
  );
});

// ---------------------------------------------------------------------------
// Missing required roles + unbound surfacing.
// ---------------------------------------------------------------------------

check('event_study: missing required roles surfaced honestly', () => {
  const w = ws([
    node({ id: 'signal', kind: 'PrimitiveNode' }),
    node({ id: 'events', operator: 'threshold_events' }),
    // Missing target, windows, output
  ]);
  const r = resolveEventStudyArtifacts(w);
  const missing = new Set(r.missingRequiredRoles);
  assertTruthy(missing.has('target'), 'target missing');
  assertTruthy(missing.has('windows'), 'windows missing');
  assertTruthy(missing.has('output'), 'output missing');
  assertFalsy(missing.has('signal'), 'signal bound');
  assertFalsy(missing.has('events'), 'events bound');
});

check('event_study: unbound nodes returned for "Other artifacts"', () => {
  const w = ws([
    node({ id: 'signal', kind: 'PrimitiveNode' }),
    node({ id: 'target', kind: 'PrimitiveNode' }),
    node({ id: 'events', operator: 'threshold_events' }),
    node({ id: 'windows', operator: 'event_windows' }),
    node({ id: 'aggregate', operator: 'conditional_aggregate' }),
    node({ id: 'compare', operator: 'series_arithmetic' }),
    // Extra nodes the resolver should leave unbound.
    node({ id: 'extra_aux', operator: 'normalize_series' }),
    node({ id: 'second_aux', operator: 'rolling_mean' }),
  ]);
  const r = resolveEventStudyArtifacts(w);
  const ub = new Set(r.unboundNodes.map((n) => n.node_id));
  assertTruthy(ub.has('extra_aux'), 'extra_aux unbound');
  assertTruthy(ub.has('second_aux'), 'second_aux unbound');
  assertFalsy(ub.has('signal'), 'signal NOT unbound');
});

// ---------------------------------------------------------------------------
// Backtest + Regime — sanity-check the canonical paths still work.
// ---------------------------------------------------------------------------

check('backtest: canonical IDs resolve every role + no missing', () => {
  const w = ws([
    node({ id: 'signal', kind: 'PrimitiveNode' }),
    node({ id: 'events', operator: 'threshold_events' }),
    node({ id: 'trades', operator: 'construct_trades' }),
    node({
      id: 'price_panel',
      kind: 'PrimitiveNode',
      tool: 'build_sovereign_yield_panel_tool',
    }),
    node({
      id: 'financing',
      kind: 'PrimitiveNode',
      tool: 'compute_financing_rate_tool',
    }),
    node({ id: 'evaluate', operator: 'evaluate_trades' }),
    node({ id: 'summarize', operator: 'summarize_trades' }),
  ]);
  const r = resolveBacktestArtifacts(w);
  assertEqual(r.missingRequiredRoles, [], 'no missing required roles');
  assertEqual(r.roles.trades?.node_id, 'trades', 'trades');
  assertEqual(r.roles.summarize?.node_id, 'summarize', 'summarize');
});

check('regime: canonical IDs resolve every role + no missing', () => {
  const w = ws([
    node({ id: 'lhs', kind: 'PrimitiveNode' }),
    node({ id: 'rhs', kind: 'PrimitiveNode' }),
    node({ id: 'regime_signal', kind: 'PrimitiveNode' }),
    node({ id: 'relationship', operator: 'rolling_regression' }),
    node({ id: 'beta', operator: 'select_from_series_set' }),
    node({ id: 'high_mask', operator: 'threshold_events' }),
    node({ id: 'low_mask', operator: 'threshold_events' }),
    node({ id: 'high_summary', operator: 'summarize_series' }),
    node({ id: 'low_summary', operator: 'summarize_series' }),
    node({ id: 'compare', operator: 'series_arithmetic' }),
  ]);
  const r = resolveRegimeArtifacts(w);
  assertEqual(r.missingRequiredRoles, [], 'no missing');
  assertEqual(
    r.roles.relationship?.node_id,
    'relationship',
    'relationship',
  );
});

// ---------------------------------------------------------------------------
// Runner
// ---------------------------------------------------------------------------

export function runAllResolveWorkflowArtifactsTests(): void {
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
    `\nresolveWorkflowArtifacts coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(
      `${failed} resolveWorkflowArtifacts check(s) failed`,
    );
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllResolveWorkflowArtifactsTests();
}
