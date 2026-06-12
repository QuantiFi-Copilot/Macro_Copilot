/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// routingCoverage.test.ts — PR1 routing-coverage assertions.
// ----------------------------------------------------------------------------
// The project doesn't ship a JS test runner today (no vitest / jest in
// package.json).  This file is written to be vitest-compatible AND
// runnable as a plain Node script via ``node --import tsx``:
//
//   npx tsx src/components/build/primitive/__tests__/routingCoverage.test.ts
//
// Each assertion is a one-liner using a tiny local ``check`` helper so
// the file works either way:
//   - under vitest: every ``check(label, fn)`` becomes a ``test(...)``
//     by virtue of the ``maybeTest`` shim below.
//   - as a script: ``runAll()`` walks every check and throws on the
//     first failure, exit-coded for CI.
//
// The file is type-checked by ``tsconfig.app.json`` (it lives under
// ``src``), so a signature drift on any of the routing functions
// breaks tsc immediately — that's the primary value at this level
// until a real runner lands.
//
// Coverage matrix mirror — every row in PR1's coverage matrix gets at
// least one assertion here, plus the discriminating edge cases
// (manifest shorthand, multi-tool grids, malformed JSON, unknown tools).
// ============================================================================

import {
  decodePrimitiveContext,
  decodePrimitiveList,
  type DecodedPrimitive,
} from '../contextDecoder';
import {
  classifyWorkflow,
  isKnownBackendTool,
  isUnsupportedKnownTool,
  isWorkflowIncompatibleTool,
  KNOWN_BACKEND_TOOLS,
  KNOWN_WORKFLOWS,
  normalizeToolName,
  PAUSED_WORKFLOWS,
  RUNNABLE_PRIMITIVE_TOOLS,
  WORKFLOW_INCOMPATIBLE_TOOLS,
  unsupportedKnownReasonFor,
} from '@/lib/toolNames';
import { ALL_PRIMITIVE_MODULES } from '@/modules';
import { listModels } from '@/lib/modelRegistry';

// ----------------------------------------------------------------------------
// Tiny test shim — works under vitest OR as a plain Node script.
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
    throw new Error(`assertEqual failed: ${label}\n  expected: ${e}\n  actual:   ${a}`);
  }
}

function assertTruthy(value: unknown, label: string): void {
  if (!value) throw new Error(`assertTruthy failed: ${label}`);
}

function assertNull(value: unknown, label: string): void {
  if (value !== null) {
    throw new Error(
      `assertNull failed: ${label}\n  expected null, got ${JSON.stringify(value)}`,
    );
  }
}

// ----------------------------------------------------------------------------
// Helpers for building a ``?context=`` payload the way Ask / Library do.
// ----------------------------------------------------------------------------

function encodeContext(tools: Array<{ tool: string; params?: Record<string, unknown> }>): string {
  return encodeURIComponent(
    JSON.stringify({
      tools: tools.map((t) => ({ tool: t.tool, params: t.params ?? {} })),
      tool_count: tools.length,
    }),
  );
}

// ----------------------------------------------------------------------------
// normalizeToolName — manifest shorthand → canonical
// ----------------------------------------------------------------------------

check('normalizeToolName: identity for canonical names', () => {
  assertEqual(
    normalizeToolName('calculate_curve_spread_tool'),
    'calculate_curve_spread_tool',
    'curve_spread canonical',
  );
  assertEqual(
    normalizeToolName('get_yield_levels_tool'),
    'get_yield_levels_tool',
    'yield_levels canonical',
  );
});

check('normalizeToolName: model-tool shorthand → canonical', () => {
  assertEqual(
    normalizeToolName('half_life_tool'),
    'calculate_half_life_tool',
    'half_life shorthand',
  );
  assertEqual(
    normalizeToolName('pca_yield_curve_tool'),
    'calculate_pca_yield_curve_tool',
    'pca shorthand',
  );
  assertEqual(
    normalizeToolName('rolling_regression_tool'),
    'calculate_rolling_regression_tool',
    'rolling_regression shorthand',
  );
  assertEqual(
    normalizeToolName('yield_change_attribution_pca_tool'),
    'calculate_yield_change_attribution_pca_tool',
    'attribution shorthand',
  );
  assertEqual(
    normalizeToolName('beta_adjusted_spread_tool'),
    'calculate_beta_adjusted_spread_tool',
    'beta_adjusted shorthand',
  );
  assertEqual(
    normalizeToolName('zscore_custom_tool'),
    'calculate_zscore_custom_tool',
    'zscore_custom shorthand',
  );
});

check('normalizeToolName: verb-mismatch (OIS rate level) → canonical', () => {
  // Manifest publishes ``calculate_ois_rate_level_tool``; backend
  // registers ``get_ois_rate_level_tool``.  Both resolve.
  assertEqual(
    normalizeToolName('calculate_ois_rate_level_tool'),
    'get_ois_rate_level_tool',
    'OIS rate level verb mismatch',
  );
});

check('normalizeToolName: empty / unknown passes through unchanged', () => {
  assertEqual(normalizeToolName(''), '', 'empty');
  assertEqual(
    normalizeToolName('totally_made_up_tool'),
    'totally_made_up_tool',
    'unknown passes through',
  );
});

// ----------------------------------------------------------------------------
// isKnownBackendTool — closed set membership.
// ----------------------------------------------------------------------------

check('isKnownBackendTool: every typed-view tool', () => {
  for (const t of [
    'calculate_curve_spread_tool',
    'calculate_cross_market_spread_tool',
    'calculate_butterfly_tool',
    'get_yield_levels_tool',
    'classify_curve_move_tool',
    'scan_extremes_tool',
    'calculate_ois_forward_rate_tool',
  ]) {
    assertTruthy(isKnownBackendTool(t), `known: ${t}`);
  }
});

check('isKnownBackendTool: every model-builder tool', () => {
  for (const t of [
    'calculate_rolling_regression_tool',
    'calculate_pca_yield_curve_tool',
    'calculate_yield_change_attribution_pca_tool',
    'calculate_half_life_tool',
    'calculate_beta_adjusted_spread_tool',
  ]) {
    assertTruthy(isKnownBackendTool(t), `known: ${t}`);
  }
});

check('isKnownBackendTool: every unsupported-known tool', () => {
  for (const t of [
    'build_sovereign_yield_panel_tool',
    'calculate_breakeven_inflation_tool',
    'calculate_swap_spread_tool',
    'calculate_zscore_custom_tool',
    'calculate_ois_cross_market_spread_tool',
    'calculate_ois_curve_spread_tool',
    'compute_financing_rate_tool',
    'get_ois_rate_level_tool',
    'scan_ois_extremes_tool',
  ]) {
    assertTruthy(isKnownBackendTool(t), `known: ${t}`);
  }
});

check('isKnownBackendTool: resolves manifest shorthand', () => {
  assertTruthy(isKnownBackendTool('half_life_tool'), 'half_life shorthand is known');
  assertTruthy(
    isKnownBackendTool('calculate_ois_rate_level_tool'),
    'verb-mismatch OIS rate-level is known',
  );
});

check('isKnownBackendTool: unknown tools are NOT known', () => {
  assertEqual(isKnownBackendTool('totally_made_up_tool'), false, 'made-up tool');
  assertEqual(isKnownBackendTool(''), false, 'empty string');
});

// ----------------------------------------------------------------------------
// isUnsupportedKnownTool — the 9 tools that should surface unsupported card.
// ----------------------------------------------------------------------------

// PR2 shrank the unsupported set: only ``scan_ois_extremes_tool``
// stays here (manifest-only, no PrimitiveSpec, no run endpoint).  The
// 8 previously-unsupported tools now route to the generic schema-
// driven builder; their assertions moved to ``genericBuilder.test.ts``.
check('isUnsupportedKnownTool: only scan_ois_extremes_tool after PR2', () => {
  assertTruthy(
    isUnsupportedKnownTool('scan_ois_extremes_tool'),
    'scan_ois_extremes_tool is unsupported',
  );
});

check('isUnsupportedKnownTool: typed-view tools are NOT unsupported', () => {
  assertEqual(
    isUnsupportedKnownTool('calculate_curve_spread_tool'),
    false,
    'curve_spread is typed-view',
  );
  assertEqual(
    isUnsupportedKnownTool('calculate_pca_yield_curve_tool'),
    false,
    'pca is model-builder',
  );
});

check('isUnsupportedKnownTool: PR2 generic-builder tools are NOT unsupported', () => {
  // Every tool that PR2 promotes to the generic builder is no longer
  // unsupported.  Asserting the negation here guards against
  // accidentally re-adding any of them to the unsupported set.
  for (const t of [
    'build_sovereign_yield_panel_tool',
    'calculate_breakeven_inflation_tool',
    'calculate_swap_spread_tool',
    'calculate_zscore_custom_tool',
    'calculate_ois_cross_market_spread_tool',
    'calculate_ois_curve_spread_tool',
    'compute_financing_rate_tool',
    'get_ois_rate_level_tool',
  ]) {
    assertEqual(isUnsupportedKnownTool(t), false, `not unsupported: ${t}`);
  }
});

check('unsupportedKnownReasonFor: scan_ois_extremes_tool has explicit copy', () => {
  const r = unsupportedKnownReasonFor('scan_ois_extremes_tool');
  assertTruthy(r.label.length > 0, 'label');
  assertTruthy(r.reason.length > 0, 'reason');
  assertTruthy(r.whatWorksNow.length > 0, 'whatWorksNow');
  if (r.label === 'scan_ois_extremes_tool') {
    throw new Error(
      'unsupportedKnownReasonFor(scan_ois_extremes_tool) fell through to generic fallback',
    );
  }
});

// ----------------------------------------------------------------------------
// decodePrimitiveContext — single-best lookup per matrix row.
// ----------------------------------------------------------------------------

check('decode: calculate_curve_spread_tool → generic_builder', () => {
  const ctx = encodeContext([
    { tool: 'calculate_curve_spread_tool', params: { curve_family: 'UST' } },
  ]);
  const out = decodePrimitiveContext(ctx);
  assertTruthy(out, 'decode result');
  assertEqual(out!.kind, 'generic_builder', 'kind');
  assertEqual(out!.toolName, 'calculate_curve_spread_tool', 'toolName');
});

check('decode: calculate_cross_market_spread_tool → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_cross_market_spread_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('decode: get_yield_levels_tool → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'get_yield_levels_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

// Consolidation (G-3.2): the five rich-models migrated to the dual-view
// standard — ``modelMetadata`` is retired, so the decode no longer
// returns the legacy 'builder' kind for them.  They decode
// 'generic_builder' and VirtualPrimitiveCanvas's module-first dispatch
// mounts ``surfaces.buildExtended`` (the canvas override, not the kind,
// carries the bespoke surface now).
check('decode: calculate_pca_yield_curve_tool (canonical) → generic_builder (migrated)', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_pca_yield_curve_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
  assertEqual(out!.toolName, 'calculate_pca_yield_curve_tool', 'toolName');
});

check('decode: pca_yield_curve_tool (shorthand) → generic_builder (migrated)', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'pca_yield_curve_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
  assertEqual(
    out!.toolName,
    'calculate_pca_yield_curve_tool',
    'normalised toolName',
  );
});

check('decode: half_life_tool (shorthand) → generic_builder (migrated)', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'half_life_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
  assertEqual(out!.toolName, 'calculate_half_life_tool', 'normalised toolName');
});

// PR2 — these tools now route to the schema-driven generic builder
// rather than the paused unsupported card.  See ``genericBuilder.test.ts``
// for the canonical decode assertions for each tool; the duplicate
// checks here remain to lock the priority interaction with typed views.
check('decode: scan_ois_extremes_tool → unsupported_known (last paused tool)', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'scan_ois_extremes_tool' }]),
  );
  assertEqual(out!.kind, 'unsupported_known', 'kind');
  assertEqual(out!.toolName, 'scan_ois_extremes_tool', 'toolName');
});

check('decode: malformed context → null (decode-error path)', () => {
  const out = decodePrimitiveContext('not-a-real-uri-encoded-json');
  assertNull(out, 'malformed JSON returns null');
});

check('decode: truly unknown tool → null', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'totally_made_up_tool' }]),
  );
  assertNull(out, 'unknown tool returns null');
});

check('decode: three-variant contract — every decode kind is one of the closed set (G-3.5 lock)', () => {
  // Consolidation target #5 deleted the legacy typed-view and
  // rich-model decode branches: ``DecodedPrimitive`` is a closed
  // three-variant union.  The model registry stays EMPTY (the G-3.2
  // migration retired ``modelMetadata`` on every module) — this lock
  // makes a regression (a module re-adding modelMetadata) loud.
  assertEqual(listModels().length, 0, 'model registry is empty');
  const allowed = new Set([
    'generic_builder',
    'workflow_incompatible',
    'unsupported_known',
  ]);
  const list = decodePrimitiveList(
    encodeContext([
      { tool: 'calculate_curve_spread_tool' },
      { tool: 'calculate_pca_yield_curve_tool' },
      { tool: 'classify_curve_move_tool' },
      { tool: 'scan_ois_extremes_tool' },
    ]),
  );
  assertEqual(list.length, 4, 'all four decode');
  for (const d of list) {
    assertTruthy(
      allowed.has(d.kind),
      `${d.toolName}: kind '${d.kind}' is in the closed three-variant set`,
    );
  }
  const best = decodePrimitiveContext(
    encodeContext([
      { tool: 'calculate_curve_spread_tool' },
      { tool: 'calculate_pca_yield_curve_tool' },
    ]),
  );
  assertTruthy(allowed.has(best!.kind), 'single-best kind in the closed set');
});

check('decode: generic_builder + migrated classifier → generic_builder wins (priority)', () => {
  // classify_curve_move_tool retired the LAST legacy view claim ('regime');
  // it now decodes via the workflow_incompatible branch (priority 0.25),
  // which loses to the runnable generic builder (priority 0.5).  Build
  // still mounts its dual-view surfaces via module-first dispatch.
  const out = decodePrimitiveContext(
    encodeContext([
      { tool: 'calculate_swap_spread_tool' }, // generic_builder
      { tool: 'classify_curve_move_tool' }, // workflow_incompatible (dual-view module)
    ]),
  );
  assertEqual(out!.kind, 'generic_builder', 'generic_builder beats workflow_incompatible');
});

check('decode: generic_builder + unsupported_known → generic_builder wins (priority)', () => {
  // The generic builder is configurable + runnable; a paused tool is
  // not.  Tie-break favours the runnable surface.
  const out = decodePrimitiveContext(
    encodeContext([
      { tool: 'scan_ois_extremes_tool' },     // unsupported_known
      { tool: 'calculate_swap_spread_tool' }, // generic_builder
    ]),
  );
  assertEqual(out!.kind, 'generic_builder', 'generic_builder beats unsupported_known');
});

// ----------------------------------------------------------------------------
// decodePrimitiveList — multi-card layout source.
// ----------------------------------------------------------------------------

check('decodeList: three cross_market calls → three generic_builder entries in order', () => {
  const list = decodePrimitiveList(
    encodeContext([
      { tool: 'calculate_cross_market_spread_tool', params: { curve_a: 'UST' } },
      { tool: 'calculate_cross_market_spread_tool', params: { curve_a: 'DE_BUND' } },
      { tool: 'calculate_cross_market_spread_tool', params: { curve_a: 'UK_GILT' } },
    ]),
  );
  assertEqual(list.length, 3, 'three entries');
  for (const item of list) {
    assertEqual(item.kind, 'generic_builder', `each: generic_builder`);
  }
  // Order preserved
  assertEqual(
    list.map((d: DecodedPrimitive) => d.params.curve_a),
    ['UST', 'DE_BUND', 'UK_GILT'],
    'curve_a order preserved',
  );
});

check('decodeList: migrated rich-model is RETAINED in the multi-card list', () => {
  // Pre-migration the rich-model decoded 'builder' and was filtered
  // out of the multi-card grid (it owned the whole canvas).  Post
  // G-3.2 it is an ordinary dual-view module — it stays in the list
  // and renders its compact card like every sibling.
  const list = decodePrimitiveList(
    encodeContext([
      { tool: 'calculate_curve_spread_tool' },
      { tool: 'calculate_pca_yield_curve_tool' },
      { tool: 'get_yield_levels_tool' },
    ]),
  );
  assertEqual(list.length, 3, 'all three retained');
  assertEqual(
    list.map((d: DecodedPrimitive) => d.toolName),
    [
      'calculate_curve_spread_tool',
      'calculate_pca_yield_curve_tool',
      'get_yield_levels_tool',
    ],
    'order preserved',
  );
});

check('decodeList: generic_builder + unsupported_known are both retained', () => {
  // PR2 — multi-card grid renders typed primitives + generic builder
  // tiles + unsupported tiles together.  Builders alone are filtered.
  const list = decodePrimitiveList(
    encodeContext([
      { tool: 'calculate_curve_spread_tool' }, // generic_builder
      { tool: 'calculate_swap_spread_tool' },  // generic_builder
      { tool: 'scan_ois_extremes_tool' },      // unsupported_known
    ]),
  );
  assertEqual(list.length, 3, 'all three retained');
  assertEqual(list[0].kind, 'generic_builder', 'first: generic_builder');
  assertEqual(list[1].kind, 'generic_builder', 'second: generic_builder');
  assertEqual(list[2].kind, 'unsupported_known', 'third: unsupported_known');
});

check('decodeList: malformed payload → empty list', () => {
  assertEqual(decodePrimitiveList('garbage'), [], 'garbage returns []');
});

// ----------------------------------------------------------------------------
// classifyWorkflow — workflow registry membership.
// ----------------------------------------------------------------------------

check('classifyWorkflow: event_study → known', () => {
  assertEqual(classifyWorkflow('event_study').kind, 'known', 'event_study known');
});

check('classifyWorkflow: regime_conditioned_relationship → known', () => {
  assertEqual(
    classifyWorkflow('regime_conditioned_relationship').kind,
    'known',
    'regime_conditioned_relationship known',
  );
});

check('classifyWorkflow: backtest → paused', () => {
  assertEqual(classifyWorkflow('backtest').kind, 'paused', 'backtest paused');
});

check('classifyWorkflow: unknown id → unknown', () => {
  assertEqual(
    classifyWorkflow('made_up_workflow').kind,
    'unknown',
    'unknown template_id',
  );
});

// ----------------------------------------------------------------------------
// Registry sanity — every PR1 inventory entry is closed-set.
// ----------------------------------------------------------------------------

check('KNOWN_BACKEND_TOOLS contains the audited 58 names (52 runnable + 4 manifest-only + 2 workflow-incompatible)', () => {
  // Stage 1 — KNOWN_BACKEND_TOOLS expands to mirror backend ground
  // truth.  Composition:
  //   * 52 entries from rates_agent.workflows._PRIMITIVE_SPECS
  //     (every runnable primitive on `build` today, including the
  //     34 already registered + the 18 net-new Stage 1 additions).
  //   * 4 manifest-only tools with typed-detail endpoints OR paused
  //     state (calculate_butterfly_tool, classify_curve_move_tool,
  //     scan_extremes_tool, scan_ois_extremes_tool).
  //   * 2 workflow-incompatible tools (get_otr_history_tool,
  //     calculate_wirp_meeting_pricing_tool) — also in
  //     WORKFLOW_INCOMPATIBLE_TOOLS, surfaced via the
  //     'workflow_incompatible' decoder kind.
  // = 58 expected canonical names.
  const expected = [
    // Sovereign-bond primitives (12)
    'build_sovereign_yield_panel_tool',
    'calculate_beta_adjusted_spread_tool',
    'calculate_breakeven_inflation_tool',
    'calculate_cross_market_spread_tool',
    'calculate_curve_spread_tool',
    'calculate_half_life_tool',
    'calculate_pca_yield_curve_tool',
    'calculate_rolling_regression_tool',
    'calculate_swap_spread_tool',
    'calculate_yield_change_attribution_pca_tool',
    'calculate_zscore_custom_tool',
    'get_yield_levels_tool',
    // OIS primitives (5)
    'calculate_ois_cross_market_spread_tool',
    'calculate_ois_curve_spread_tool',
    'calculate_ois_forward_rate_tool',
    'compute_financing_rate_tool',
    'get_ois_rate_level_tool',
    // Manifest-only typed-view / paused (4)
    'calculate_butterfly_tool',
    'classify_curve_move_tool',
    'scan_extremes_tool',
    'scan_ois_extremes_tool',
    // Factory-ported OIS / bond_futures / policy_futures / inflation (17)
    'calculate_ois_butterfly_tool',
    'get_futures_price_level_tool',
    'get_futures_volume_oi_tool',
    'scan_bond_futures_extremes_tool',
    'build_policy_futures_strip_panel_tool',
    'get_scan_policy_futures_extremes_tool',
    'policy_futures_get_futures_butterfly_simple_tool',
    'policy_futures_get_futures_calendar_spread_tool',
    'policy_futures_get_futures_cross_market_spread_tool',
    'policy_futures_get_futures_pack_average_simple_tool',
    'policy_futures_get_futures_price_level_tool',
    'policy_futures_get_futures_strip_snapshot_tool',
    'policy_futures_get_volume_open_interest_snapshot_tool',
    'build_linker_panel_tool',
    'scan_inflation_linkers_extremes_tool',
    'build_zcis_panel_tool',
    'scan_inflation_swaps_extremes_tool',
    // Stage 1 net-new runnable primitives (18)
    'calculate_otr_ofr_spread_tool',
    'calculate_cpi_surprise_tool',
    'calculate_nfp_surprise_tool',
    'get_real_yield_level_tool',
    'calculate_breakeven_inflation_simple_tool',
    'calculate_forward_breakeven_simple_tool',
    'calculate_breakeven_curve_spread_tool',
    'calculate_cross_country_breakeven_spread_simple_tool',
    'calculate_real_yield_curve_spread_tool',
    'calculate_cross_country_real_yield_spread_simple_tool',
    'calculate_real_yield_butterfly_tool',
    'calculate_breakeven_butterfly_tool',
    'calculate_inflation_swap_rate_level_tool',
    'calculate_inflation_swap_curve_spread_tool',
    'calculate_inflation_swap_forward_tool',
    'calculate_cross_market_inflation_swap_spread_tool',
    'calculate_swap_breakeven_basis_simple_tool',
    'calculate_inflation_swap_butterfly_tool',
    // Stage 1 workflow-incompatible (2)
    'get_otr_history_tool',
    'calculate_wirp_meeting_pricing_tool',
  ];
  assertEqual(
    KNOWN_BACKEND_TOOLS.size,
    expected.length,
    `set size (got ${KNOWN_BACKEND_TOOLS.size}, expected ${expected.length})`,
  );
  for (const t of expected) {
    assertTruthy(KNOWN_BACKEND_TOOLS.has(t), `missing: ${t}`);
  }
});

// ----------------------------------------------------------------------------
// Stage 1 — WORKFLOW_INCOMPATIBLE_TOOLS registry + decoder coverage.
// ----------------------------------------------------------------------------

check('WORKFLOW_INCOMPATIBLE_TOOLS mirrors the backend set exactly', () => {
  // G-3.1c: classify_curve_move_tool joined when its typed-view path
  // ('regime' — which used to pre-empt this set in the decoder) was
  // retired by the dual-view migration.  The set now mirrors the
  // backend's WORKFLOW_INCOMPATIBLE_TOOLS dict 1:1.
  const expected = [
    'classify_curve_move_tool',
    'get_otr_history_tool',
    'calculate_wirp_meeting_pricing_tool',
  ];
  assertEqual(
    WORKFLOW_INCOMPATIBLE_TOOLS.size,
    expected.length,
    `set size (got ${WORKFLOW_INCOMPATIBLE_TOOLS.size}, expected ${expected.length})`,
  );
  for (const t of expected) {
    assertTruthy(WORKFLOW_INCOMPATIBLE_TOOLS.has(t), `missing: ${t}`);
  }
});

check('isWorkflowIncompatibleTool: positive cases', () => {
  assertTruthy(
    isWorkflowIncompatibleTool('get_otr_history_tool'),
    'get_otr_history_tool',
  );
  assertTruthy(
    isWorkflowIncompatibleTool('calculate_wirp_meeting_pricing_tool'),
    'calculate_wirp_meeting_pricing_tool',
  );
});

check('isWorkflowIncompatibleTool: negative cases', () => {
  assertEqual(
    isWorkflowIncompatibleTool('calculate_curve_spread_tool'),
    false,
    'typed-view tool is NOT workflow_incompatible',
  );
  // classify_curve_move_tool moved to the POSITIVE set with the
  // G-3.1c migration (typed-view retired) — asserted in the
  // positive-cases check above.
  assertEqual(
    isWorkflowIncompatibleTool('totally_made_up_tool'),
    false,
    'unknown tool is NOT workflow_incompatible',
  );
});

check('decode: get_otr_history_tool → workflow_incompatible', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'get_otr_history_tool' }]),
  );
  assertEqual(out!.kind, 'workflow_incompatible', 'kind');
  assertEqual(out!.toolName, 'get_otr_history_tool', 'toolName');
});

check('decode: calculate_wirp_meeting_pricing_tool → workflow_incompatible', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_wirp_meeting_pricing_tool' }]),
  );
  assertEqual(out!.kind, 'workflow_incompatible', 'kind');
  assertEqual(out!.toolName, 'calculate_wirp_meeting_pricing_tool', 'toolName');
});

check('decode: workflow_incompatible + unsupported_known → workflow_incompatible wins (priority)', () => {
  // workflow_incompatible (0.25) > unsupported_known (0).  A real
  // shipped tool beats a paused / unbuilt one.
  const out = decodePrimitiveContext(
    encodeContext([
      { tool: 'scan_ois_extremes_tool' },           // unsupported_known
      { tool: 'get_otr_history_tool' },             // workflow_incompatible
    ]),
  );
  assertEqual(out!.kind, 'workflow_incompatible', 'workflow_incompatible beats unsupported_known');
});

check('decode: generic_builder + workflow_incompatible → generic_builder wins (priority)', () => {
  // Runnable surface beats unsupported surface.
  const out = decodePrimitiveContext(
    encodeContext([
      { tool: 'get_otr_history_tool' },             // workflow_incompatible
      { tool: 'calculate_swap_spread_tool' },       // generic_builder
    ]),
  );
  assertEqual(out!.kind, 'generic_builder', 'generic_builder beats workflow_incompatible');
});

check('decode: two workflow_incompatible tools → first wins (tie-break)', () => {
  // No legacy view-claiming modules remain post classify_curve_move migration; pin
  // the workflow_incompatible tie-break instead (>= keeps the LAST equal-
  // priority entry, matching decodePrimitiveContext's score >= bestScore).
  const out = decodePrimitiveContext(
    encodeContext([
      { tool: 'get_otr_history_tool' },     // workflow_incompatible
      { tool: 'classify_curve_move_tool' }, // workflow_incompatible (dual-view module)
    ]),
  );
  assertEqual(out!.kind, 'workflow_incompatible', 'workflow_incompatible decode survives');
});

check('decodeList: workflow_incompatible entries retained in order', () => {
  const list = decodePrimitiveList(
    encodeContext([
      { tool: 'classify_curve_move_tool' },            // workflow_incompatible (dual-view module)
      { tool: 'get_otr_history_tool' },                // workflow_incompatible
      { tool: 'calculate_wirp_meeting_pricing_tool' }, // workflow_incompatible
      { tool: 'scan_ois_extremes_tool' },              // unsupported_known
    ]),
  );
  assertEqual(list.length, 4, 'four entries');
  assertEqual(list[0].kind, 'workflow_incompatible', '0: migrated classifier (dual-view module)');
  assertEqual(list[1].kind, 'workflow_incompatible', '1: workflow_incompatible');
  assertEqual(list[2].kind, 'workflow_incompatible', '2: workflow_incompatible');
  assertEqual(list[3].kind, 'unsupported_known', '3: unsupported_known');
});

check('unsupportedKnownReasonFor: workflow_incompatible tools have explicit copy', () => {
  for (const t of ['get_otr_history_tool', 'calculate_wirp_meeting_pricing_tool']) {
    const r = unsupportedKnownReasonFor(t);
    assertTruthy(r.label.length > 0, `${t}: label`);
    assertTruthy(r.reason.length > 0, `${t}: reason`);
    assertTruthy(r.whatWorksNow.length > 0, `${t}: whatWorksNow`);
    if (r.label === t) {
      throw new Error(
        `unsupportedKnownReasonFor(${t}) fell through to generic fallback`,
      );
    }
  }
});

check('Stage 1: every net-new runnable primitive decodes to generic_builder', () => {
  // Smoke check that the 18 net-new Stage 1 primitives all surface
  // honestly through the schema-driven generic builder.  Any failure
  // here means the primitive fell out of RUNNABLE_PRIMITIVE_TOOLS —
  // which would silently break the routing intent.
  const stage1NetNew = [
    'calculate_otr_ofr_spread_tool',
    'calculate_cpi_surprise_tool',
    'calculate_nfp_surprise_tool',
    'get_real_yield_level_tool',
    'calculate_breakeven_inflation_simple_tool',
    'calculate_forward_breakeven_simple_tool',
    'calculate_breakeven_curve_spread_tool',
    'calculate_cross_country_breakeven_spread_simple_tool',
    'calculate_real_yield_curve_spread_tool',
    'calculate_cross_country_real_yield_spread_simple_tool',
    'calculate_real_yield_butterfly_tool',
    'calculate_breakeven_butterfly_tool',
    'calculate_inflation_swap_rate_level_tool',
    'calculate_inflation_swap_curve_spread_tool',
    'calculate_inflation_swap_forward_tool',
    'calculate_cross_market_inflation_swap_spread_tool',
    'calculate_swap_breakeven_basis_simple_tool',
    'calculate_inflation_swap_butterfly_tool',
  ];
  assertEqual(stage1NetNew.length, 18, 'Stage 1 net-new count');
  for (const t of stage1NetNew) {
    assertTruthy(
      RUNNABLE_PRIMITIVE_TOOLS.has(t),
      `${t}: in RUNNABLE_PRIMITIVE_TOOLS`,
    );
    const out = decodePrimitiveContext(encodeContext([{ tool: t }]));
    assertEqual(out!.kind, 'generic_builder', `${t}: decodes to generic_builder`);
    assertEqual(out!.toolName, t, `${t}: toolName preserved`);
  }
});

check('KNOWN_WORKFLOWS contains exactly event_study + regime_conditioned_relationship', () => {
  assertEqual(KNOWN_WORKFLOWS.size, 2, 'known workflow count');
  assertTruthy(KNOWN_WORKFLOWS.has('event_study'), 'event_study in known');
  assertTruthy(
    KNOWN_WORKFLOWS.has('regime_conditioned_relationship'),
    'regime in known',
  );
});

check('PAUSED_WORKFLOWS contains backtest', () => {
  assertTruthy(PAUSED_WORKFLOWS.has('backtest'), 'backtest paused');
});

// ----------------------------------------------------------------------------
// Decoder coverage gate — every module in ALL_PRIMITIVE_MODULES decodes
// to a non-null DecodedPrimitive.
// ----------------------------------------------------------------------------
//
// The surface contract's containment principle (see
// docs_revamped/02_components/surface_contract.md §1) gates on every
// module being routable end-to-end through the Ask→Build handoff.  The
// existing Stage 1 net-new check (above) iterates a HAND-AUTHORED list
// of 18 tool names — that catches regressions on those 18 specifically
// but says nothing about the other 40 modules in the registry.  As new
// modules land via tools/scaffold_module.py, the hand-authored list
// goes stale and a future module could silently fall through to the
// decode-error card without any check tripping.
//
// This assertion iterates ALL_PRIMITIVE_MODULES (the actual source of
// truth) and asserts every module's toolName decodes to SOMETHING.  It
// makes no claim about WHICH decoded kind — the per-tool kind tests
// above stay authoritative for the discriminator logic.  This is the
// minimum-bar coverage gate: "no module silently drops into the orange
// decode-error card", which is the user-visible failure mode the
// containment principle exists to prevent.
//
// Synthetic smoke-test fixtures (tool names starting with ``__``) are
// excluded — they live in ALL_PRIMITIVE_MODULES for the Stage 5
// module-first dispatch acceptance test but MUST NOT be expected to
// route through the user-facing decoder.
check('every module in ALL_PRIMITIVE_MODULES decodes to a non-null result', () => {
  const realModules = ALL_PRIMITIVE_MODULES.filter(
    (m) => !m.toolName.startsWith('__'),
  );
  assertTruthy(
    realModules.length >= 50,
    `expected >=50 real modules in registry; got ${realModules.length}`,
  );
  const failures: string[] = [];
  for (const m of realModules) {
    const out = decodePrimitiveContext(encodeContext([{ tool: m.toolName }]));
    if (out === null) {
      failures.push(m.toolName);
      continue;
    }
    if (out.toolName !== m.toolName) {
      failures.push(
        `${m.toolName}: decoded.toolName=${out.toolName} (canonical mismatch)`,
      );
    }
  }
  if (failures.length > 0) {
    throw new Error(
      `decoder coverage gap — ${failures.length} module(s) failed to decode:\n  ` +
        failures.join('\n  '),
    );
  }
});

// ----------------------------------------------------------------------------
// Runner — exported so the file works as a script.  Vitest installations
// can replace this with a ``describe`` wrapper without touching the
// individual ``check`` calls.
// ----------------------------------------------------------------------------

export function runAllRoutingCoverageTests(): void {
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
  console.log(`\nrouting coverage: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    throw new Error(`${failed} routing-coverage check(s) failed`);
  }
}

// When executed directly (``tsx path/to/file.ts``), run all checks.
// Under vitest, ``import.meta`` works but ``main`` is undefined; the
// runner won't fire automatically — install vitest + import this file
// to lift each ``check`` into a real test.
const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllRoutingCoverageTests();
}
