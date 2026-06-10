/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// buildE2E.test.ts — PR8 closure: end-to-end Build flow matrix.
// ----------------------------------------------------------------------------
// Walks every row of the Build QA matrix (``docs/architecture/
// build_workspace_qa.md``) through the pure-logic layers — decoder,
// builder kind, dashboard kind, artifact resolver, override-state
// machine — so a future regression that breaks a row produces a CI
// failure rather than a manual-QA escape.
//
// Why pure-logic e2e (no DOM)
// ---------------------------
// The repo doesn't ship a JS test runner today (no vitest / jest /
// playwright), and PR8's brief explicitly says "do not add a heavy
// new testing dependency only for this PR."  Every Build surface
// already has a pure-logic seam:
//
//   - URL ``?context=`` →   decodePrimitiveContext / decodePrimitiveList
//   - URL ``?builder=`` →   hasModelMetadata
//   - URL ``?workflow=`` →  classifyWorkflow
//   - URL ``/<slug>`` →     resolveWorkflowDashboard +
//                            resolve{EventStudy,Regime,Backtest}Artifacts
//   - Parameters edits →    overridesReducer + overridesToServerPatch
//
// We exercise each seam with realistic fixtures and assert the
// matrix's "expected" column.  Any regression that breaks a row
// fails this file before it reaches the browser-checklist.
// ============================================================================

import {
  decodePrimitiveContext,
  decodePrimitiveList,
} from '../primitive/contextDecoder';
import { classifyWorkflow } from '@/lib/toolNames';
import { hasModelMetadata } from '@/lib/modelRegistry';
import { buildDagModel } from '../dag/lib/buildDagModel';
import {
  resolveWorkflowDashboard,
} from '../results/lib/dashboardRegistry';
import {
  resolveEventStudyArtifacts,
  resolveRegimeArtifacts,
  resolveBacktestArtifacts,
} from '../results/lib/resolveWorkflowArtifacts';
import { deriveSlotControlsFromCard } from '../parameters/lib/deriveSlotControls';
import {
  overridesReducer,
  overridesToServerPatch,
  hasPendingOverrides,
} from '../parameters/lib/overridesState';
import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';
import type { WorkflowTemplateCard } from '@/types/workflows';

// ----------------------------------------------------------------------------
// Test shim — same pattern PR1-7 use.
// ----------------------------------------------------------------------------

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

// ----------------------------------------------------------------------------
// Fixtures — minimal canonical shapes
// ----------------------------------------------------------------------------

function node(
  id: string,
  kind: 'primitive' | 'operator',
  options: {
    operatorName?: string;
    toolName?: string;
    artifactHash?: string | null;
    artifactType?: string;
  } = {},
): NodeSummary {
  const params: Record<string, unknown> = {};
  if (options.operatorName) {
    params.operator_name = options.operatorName;
    params.params = {};
  } else if (options.toolName) {
    params.tool_name = options.toolName;
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
    artifact_hash: options.artifactHash === null
      ? null
      : options.artifactHash ?? 'a'.repeat(64),
    artifact:
      options.artifactHash === null
        ? null
        : {
            hash: options.artifactHash ?? 'a'.repeat(64),
            artifact_type: options.artifactType ?? 'Series',
            units: null,
            frequency: null,
            row_count: 100,
            byte_size: 1024,
            payload_uri: null,
            inline: true,
            created_at: '2025-01-01T00:00:00Z',
            preview_index: [],
            preview_values: [],
          },
  };
}

function workspace(args: {
  template_id?: string | null;
  bound_slot_values?: Record<string, unknown> | null;
  nodes: NodeSummary[];
  edges?: Array<{ from: string; to: string; slot?: string }>;
  focus?: string | null;
}): WorkspaceDetail {
  return {
    workspace_id: 'ws',
    slug: 'ws-slug',
    name: null,
    dag_hash: 'd'.repeat(64),
    focus_node: args.focus ?? null,
    parent_workspace_id: null,
    schema_version: 1,
    created_by: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    nodes: args.nodes,
    edges: (args.edges ?? []).map((e) => ({
      from_node: e.from,
      to_node: e.to,
      slot_name: e.slot ?? '',
    })),
    template_id: args.template_id ?? null,
    bound_slot_values: args.bound_slot_values ?? null,
  };
}

function encodeContext(
  tools: Array<{ tool: string; params?: Record<string, unknown> }>,
): string {
  return encodeURIComponent(
    JSON.stringify({
      tools: tools.map((t) => ({ tool: t.tool, params: t.params ?? {} })),
      tool_count: tools.length,
    }),
  );
}

// ----------------------------------------------------------------------------
// MATRIX ROW 2 — Library "Open in builder" on a model tool
// ----------------------------------------------------------------------------

check('row 2: ?builder=calculate_pca_yield_curve_tool → BuilderCanvas mounts via hasModelMetadata', () => {
  assertTruthy(
    hasModelMetadata('calculate_pca_yield_curve_tool'),
    'PCA is a model-registry tool',
  );
});

// ----------------------------------------------------------------------------
// MATRIX ROWS 3-5 — Workflow status handoffs
// ----------------------------------------------------------------------------

check('row 3: ?workflow=event_study → known status', () => {
  assertEqual(classifyWorkflow('event_study').kind, 'known', 'kind');
});

check('row 4: ?workflow=backtest → paused status', () => {
  assertEqual(classifyWorkflow('backtest').kind, 'paused', 'kind');
});

check('row 5: ?workflow=unknown_id → unknown status', () => {
  assertEqual(classifyWorkflow('totally_made_up').kind, 'unknown', 'kind');
});

// ----------------------------------------------------------------------------
// MATRIX ROWS 6-7 — Library typed-view tools
// ----------------------------------------------------------------------------

check('row 6: ?context=<curve_spread> → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_curve_spread_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('row 7: ?context=<yield_levels> → generic_builder (post-migration)', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'get_yield_levels_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

// ----------------------------------------------------------------------------
// MATRIX ROWS 8-11 — Library generic-builder tools
// ----------------------------------------------------------------------------

check('row 8: ?context=<swap_spread> → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_swap_spread_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('row 9: ?context=<ois_curve_spread> → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_ois_curve_spread_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('row 10: ?context=<breakeven> → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_breakeven_inflation_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('row 11: ?context=<sovereign_yield_panel> → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'build_sovereign_yield_panel_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

// ----------------------------------------------------------------------------
// MATRIX ROW 12 — Library "Open Build (unsupported)" on a paused tool
// ----------------------------------------------------------------------------

check('row 12: ?context=<scan_ois_extremes_tool> → unsupported_known (paused)', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'scan_ois_extremes_tool' }]),
  );
  assertEqual(out!.kind, 'unsupported_known', 'kind');
});

// ----------------------------------------------------------------------------
// MATRIX ROWS 13-14 — Ask handoffs (single + multi)
// ----------------------------------------------------------------------------

check('row 13: Ask single-tool handoff → generic_builder + params preserved', () => {
  const out = decodePrimitiveContext(
    encodeContext([
      {
        tool: 'calculate_curve_spread_tool',
        params: { curve_family: 'UST', short_tenor: '2Y', long_tenor: '10Y' },
      },
    ]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
  assertEqual(out!.params.curve_family, 'UST', 'params preserved');
  assertEqual(out!.params.short_tenor, '2Y', 'short_tenor preserved');
});

check('row 14: Ask multi-tool handoff → list with multiple entries', () => {
  const list = decodePrimitiveList(
    encodeContext([
      { tool: 'get_yield_levels_tool', params: { curve_family: 'UST' } },
      { tool: 'get_yield_levels_tool', params: { curve_family: 'DE_BUND' } },
      { tool: 'calculate_swap_spread_tool' },
    ]),
  );
  assertEqual(list.length, 3, 'three entries');
  assertEqual(list[0].kind, 'generic_builder', 'first: generic builder');
  assertEqual(list[1].kind, 'generic_builder', 'second: generic builder');
  assertEqual(list[2].kind, 'generic_builder', 'third: generic builder');
});

// ----------------------------------------------------------------------------
// MATRIX ROW 16 — event_study persisted workspace
// ----------------------------------------------------------------------------

function eventStudyWorkspace(): WorkspaceDetail {
  return workspace({
    template_id: 'event_study',
    focus: 'compare',
    bound_slot_values: {
      signal_tool_name: 'calculate_swap_spread_tool',
      threshold: 1.5,
      post_window: 5,
      signal_params: {
        sovereign_curve_family: 'UST',
        ois_curve_family: 'USD_SOFR_OIS',
        tenor: '2Y',
        lookback_days: 1825,
      },
    },
    nodes: [
      node('signal', 'primitive'),
      node('target', 'primitive'),
      node('align', 'operator', { operatorName: 'align_series' }),
      node('signal_aligned', 'operator', { operatorName: 'select_from_series_set' }),
      node('target_aligned', 'operator', { operatorName: 'select_from_series_set' }),
      node('events', 'operator', { operatorName: 'threshold_events', artifactType: 'EventSet' }),
      node('windows', 'operator', { operatorName: 'event_windows', artifactType: 'WindowedPanel' }),
      node('aggregate', 'operator', { operatorName: 'conditional_aggregate' }),
      node('unconditional_events', 'operator', { operatorName: 'threshold_events', artifactType: 'EventSet' }),
      node('unconditional_windows', 'operator', { operatorName: 'event_windows', artifactType: 'WindowedPanel' }),
      node('unconditional_aggregate', 'operator', { operatorName: 'conditional_aggregate' }),
      node('compare', 'operator', { operatorName: 'series_arithmetic' }),
    ],
    edges: [
      { from: 'signal', to: 'align', slot: 'series_1' },
      { from: 'target', to: 'align', slot: 'series_2' },
      { from: 'align', to: 'signal_aligned' },
      { from: 'align', to: 'target_aligned' },
      { from: 'signal_aligned', to: 'events', slot: 'series' },
      { from: 'events', to: 'windows', slot: 'mask' },
      { from: 'target_aligned', to: 'windows', slot: 'series' },
      { from: 'windows', to: 'aggregate' },
      { from: 'signal_aligned', to: 'unconditional_events' },
      { from: 'unconditional_events', to: 'unconditional_windows' },
      { from: 'target_aligned', to: 'unconditional_windows' },
      { from: 'unconditional_windows', to: 'unconditional_aggregate' },
      { from: 'aggregate', to: 'compare', slot: 'lhs' },
      { from: 'unconditional_aggregate', to: 'compare', slot: 'rhs' },
    ],
  });
}

check('row 16 DAG: event_study workspace → branch-aware model with multi-rank topology', () => {
  const m = buildDagModel(eventStudyWorkspace());
  assertTruthy(m.ranks >= 5, 'multiple ranks');
  assertTruthy(m.lanes >= 2, 'multiple lanes (branches)');
  assertEqual(m.warnings, [], 'no warnings');
  // Terminal is compare.
  assertEqual(m.terminalNodeIds, ['compare'], 'compare is terminal');
});

check('row 16 Results: event_study → EventStudyDashboard via registry', () => {
  assertEqual(
    resolveWorkflowDashboard(eventStudyWorkspace()),
    'event_study',
    'kind',
  );
});

check('row 16 Results: every required role binds', () => {
  const m = resolveEventStudyArtifacts(eventStudyWorkspace());
  assertEqual(m.missingRequiredRoles, [], 'no missing roles');
  assertTruthy(m.roles.signal, 'signal');
  assertTruthy(m.roles.events, 'events');
  assertTruthy(m.roles.windows, 'windows');
  assertTruthy(m.roles.output, 'output');
  // Output binds to focus_node ``compare``.
  assertEqual(m.roles.output?.node_id, 'compare', 'output is compare');
});

check('row 16 Parameters: slot schema derives editable controls', () => {
  // Mirror the event_study slot schema for the fixture.
  const card: WorkflowTemplateCard = {
    template_id: 'event_study',
    archetype: 'event_study',
    description: 'event study fixture',
    slot_schema: [
      { name: 'signal_tool_name', type: 'str', required: true, description: '' },
      { name: 'threshold', type: 'float', required: true, description: '' },
      { name: 'post_window', type: 'int', required: true, description: '' },
      { name: 'signal_params', type: 'dict', required: true, description: '' },
    ],
    terminal_artifact_type: 'Series',
    primitives_used: [],
    operators_used: [],
    node_count: 12,
    edge_count: 14,
    archetype_signature: [],
  };
  const ws = eventStudyWorkspace();
  const descriptors = deriveSlotControlsFromCard({
    boundSlotValues: ws.bound_slot_values,
    card,
  });
  const editable = descriptors.filter((d) => !d.readOnly && !d.hidden);
  // signal_tool_name + threshold + post_window + signal_params.* (4 fields)
  assertTruthy(editable.length >= 4, 'at least 4 editable descriptors');
  // signal_params expands one level — confirm at least one dict-field path.
  const hasDictField = editable.some(
    (d) => d.path.length === 2 && d.path[0] === 'signal_params',
  );
  assertTruthy(hasDictField, 'signal_params expands one level');
});

// ----------------------------------------------------------------------------
// MATRIX ROW 17 — regime_conditioned_relationship persisted workspace
// ----------------------------------------------------------------------------

function regimeWorkspace(): WorkspaceDetail {
  return workspace({
    template_id: 'regime_conditioned_relationship',
    focus: 'compare',
    bound_slot_values: {
      lhs_tool_name: 'get_yield_levels_tool',
      rhs_tool_name: 'get_yield_levels_tool',
      regime_signal_tool_name: 'calculate_curve_spread_tool',
      regression_window_days: 252,
      regime_threshold: -50,
    },
    nodes: [
      node('lhs', 'primitive'),
      node('rhs', 'primitive'),
      node('regime_signal', 'primitive'),
      node('relationship', 'operator', { operatorName: 'rolling_regression', artifactType: 'SeriesSet' }),
      node('beta', 'operator', { operatorName: 'select_from_series_set' }),
      node('regime_diff', 'operator', { operatorName: 'series_arithmetic' }),
      node('high_mask', 'operator', { operatorName: 'threshold_events', artifactType: 'EventSet' }),
      node('low_mask', 'operator', { operatorName: 'threshold_events', artifactType: 'EventSet' }),
      node('high_betas', 'operator', { operatorName: 'apply_mask' }),
      node('low_betas', 'operator', { operatorName: 'apply_mask' }),
      node('high_summary', 'operator', { operatorName: 'summarize_series', artifactType: 'Panel' }),
      node('low_summary', 'operator', { operatorName: 'summarize_series', artifactType: 'Panel' }),
      node('compare', 'operator', { operatorName: 'series_arithmetic' }),
    ],
    edges: [
      { from: 'lhs', to: 'relationship', slot: 'lhs' },
      { from: 'rhs', to: 'relationship', slot: 'rhs' },
      { from: 'relationship', to: 'beta' },
      { from: 'regime_signal', to: 'regime_diff' },
      { from: 'regime_diff', to: 'high_mask' },
      { from: 'regime_diff', to: 'low_mask' },
      { from: 'beta', to: 'high_betas' },
      { from: 'high_mask', to: 'high_betas' },
      { from: 'beta', to: 'low_betas' },
      { from: 'low_mask', to: 'low_betas' },
      { from: 'high_betas', to: 'high_summary' },
      { from: 'low_betas', to: 'low_summary' },
      { from: 'high_summary', to: 'compare' },
      { from: 'low_summary', to: 'compare' },
    ],
  });
}

check('row 17 Results: regime → RegimeRelationshipDashboard', () => {
  assertEqual(
    resolveWorkflowDashboard(regimeWorkspace()),
    'regime_conditioned_relationship',
    'kind',
  );
});

check('row 17 Resolver: every required role binds', () => {
  const m = resolveRegimeArtifacts(regimeWorkspace());
  assertEqual(m.missingRequiredRoles, [], 'no missing');
  assertTruthy(m.roles.lhs, 'lhs');
  assertTruthy(m.roles.regimeSignal, 'regime_signal');
  assertTruthy(m.roles.relationship, 'relationship');
  assertTruthy(m.roles.output, 'output');
});

check('row 17 DAG: regime workspace → multiple lanes for parallel branches', () => {
  const m = buildDagModel(regimeWorkspace());
  assertTruthy(m.lanes >= 2, 'parallel mask/model branches');
  assertEqual(m.warnings, [], 'no warnings');
});

// ----------------------------------------------------------------------------
// MATRIX ROW 18 — backtest workspace (paused)
// ----------------------------------------------------------------------------

function backtestWorkspaceMinimal(): WorkspaceDetail {
  // Simulate a paused-backtest workspace: signal + events persisted,
  // execution stages absent.  The dashboard renders the paused
  // banner + MissingArtifactCard for trades/eval/summarize.
  return workspace({
    template_id: 'backtest',
    focus: 'summarize',
    nodes: [
      node('signal', 'primitive'),
      node('events', 'operator', { operatorName: 'threshold_events', artifactType: 'EventSet' }),
    ],
    edges: [{ from: 'signal', to: 'events' }],
  });
}

check('row 18 Results: paused-backtest workspace → BacktestDashboard', () => {
  assertEqual(
    resolveWorkflowDashboard(backtestWorkspaceMinimal()),
    'backtest',
    'kind',
  );
});

check('row 18 Resolver: paused workspace reports missing execution roles', () => {
  const m = resolveBacktestArtifacts(backtestWorkspaceMinimal());
  assertEqual(m.roles.trades, null, 'no trades');
  assertEqual(m.roles.evaluate, null, 'no evaluate');
  assertEqual(m.roles.summarize, null, 'no summarize');
  const missing = m.missingRequiredRoles.sort();
  // signal + events are present; trades + summarize are required +
  // missing.
  assertTruthy(missing.includes('trades'), 'trades reported missing');
  assertTruthy(missing.includes('summarize'), 'summarize reported missing');
});

// ----------------------------------------------------------------------------
// MATRIX ROW 19 — unsupported / legacy workspace
// ----------------------------------------------------------------------------

check('row 19: legacy workspace (no template_id, generic topology) → generic', () => {
  const ws = workspace({
    template_id: null,
    nodes: [
      node('a', 'primitive'),
      node('b', 'operator', { operatorName: 'series_arithmetic' }),
    ],
    edges: [{ from: 'a', to: 'b' }],
  });
  assertEqual(resolveWorkflowDashboard(ws), 'generic', 'kind');
});

// ----------------------------------------------------------------------------
// MATRIX ROW 20 — Parameter override + fork payload (PR5)
// ----------------------------------------------------------------------------

check('row 20: scalar slot override produces correct slot_overrides patch', () => {
  let state = {};
  state = overridesReducer(state, {
    type: 'set',
    descriptor: {
      path: ['threshold'],
      label: 'Threshold',
      meta: { kind: 'threshold', min: 0, max: 5, step: 0.1 },
      currentValue: 1.5,
    },
    value: 2.0,
  });
  assertEqual(hasPendingOverrides(state), true, 'pending');
  const patch = overridesToServerPatch(state);
  assertEqual(patch.slot_overrides, { threshold: 2.0 }, 'scalar slot');
  assertEqual(patch.slot_dict_overrides, {}, 'no dict');
});

check('row 20: dict-field override produces correct slot_dict_overrides patch', () => {
  let state = {};
  state = overridesReducer(state, {
    type: 'set',
    descriptor: {
      path: ['signal_params', 'lookback_days'],
      label: 'Lookback Days',
      meta: { kind: 'lookback_days', min: 30, max: 7300 },
      currentValue: 1825,
    },
    value: 730,
  });
  const patch = overridesToServerPatch(state);
  assertEqual(patch.slot_overrides, {}, 'no scalar');
  assertEqual(
    patch.slot_dict_overrides,
    { signal_params: { lookback_days: 730 } },
    'dict override',
  );
});

// ----------------------------------------------------------------------------
// MATRIX ROWS 38-40 — DAG fallback / cycle / orphan banners
// ----------------------------------------------------------------------------

check('rows 38-40: DAG warnings surface for orphan / missing / cycle cases', () => {
  // Orphan edges.
  const ws1 = workspace({
    template_id: null,
    nodes: [node('A', 'primitive'), node('B', 'operator', { operatorName: 'op' })],
    edges: [
      { from: 'A', to: 'B' },
      { from: 'A', to: 'GHOST' },
    ],
  });
  const m1 = buildDagModel(ws1);
  assertTruthy(m1.warnings.includes('orphan_edges'), 'orphan warning');

  // Missing edges fallback.
  const ws2 = workspace({
    template_id: null,
    nodes: [
      node('A', 'primitive'),
      node('B', 'operator', { operatorName: 'op' }),
    ],
    edges: [],
  });
  const m2 = buildDagModel(ws2);
  assertTruthy(
    m2.warnings.includes('missing_edges_fallback'),
    'missing_edges_fallback',
  );

  // Cycle.
  const ws3 = workspace({
    template_id: null,
    nodes: [
      node('A', 'operator', { operatorName: 'op' }),
      node('B', 'operator', { operatorName: 'op' }),
    ],
    edges: [
      { from: 'A', to: 'B' },
      { from: 'B', to: 'A' },
    ],
  });
  const m3 = buildDagModel(ws3);
  assertTruthy(m3.warnings.includes('cycle_detected'), 'cycle warning');
});

// ----------------------------------------------------------------------------
// Composite: an entire event_study lifecycle through every layer
// ----------------------------------------------------------------------------

check('composite: event_study lifecycle (URL → dashboard → resolver → fork payload)', () => {
  const ws = eventStudyWorkspace();

  // 1. Dashboard selection.
  const kind = resolveWorkflowDashboard(ws);
  assertEqual(kind, 'event_study', 'dashboard');

  // 2. DAG model.
  const dag = buildDagModel(ws);
  assertEqual(dag.warnings, [], 'no DAG warnings');
  assertTruthy(dag.terminalNodeIds.includes('compare'), 'terminal');

  // 3. Artifact resolver.
  const roles = resolveEventStudyArtifacts(ws);
  assertEqual(roles.missingRequiredRoles, [], 'no missing roles');

  // 4. Parameter override + fork payload.
  let state = {};
  state = overridesReducer(state, {
    type: 'set',
    descriptor: {
      path: ['signal_params', 'lookback_days'],
      label: 'Lookback',
      meta: { kind: 'lookback_days', min: 30, max: 7300 },
      currentValue: 1825,
    },
    value: 730,
  });
  const patch = overridesToServerPatch(state);
  assertEqual(
    patch.slot_dict_overrides,
    { signal_params: { lookback_days: 730 } },
    'fork payload',
  );
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export function runAllBuildE2ETests(): void {
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
  console.log(`\nbuild e2e coverage: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    throw new Error(`${failed} build-e2e check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllBuildE2ETests();
}
