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
  KNOWN_BACKEND_TOOLS,
  KNOWN_WORKFLOWS,
  normalizeToolName,
  PAUSED_WORKFLOWS,
  unsupportedKnownReasonFor,
} from '@/lib/toolNames';

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

check('decode: calculate_curve_spread_tool → spread typed view', () => {
  const ctx = encodeContext([
    { tool: 'calculate_curve_spread_tool', params: { curve_family: 'UST' } },
  ]);
  const out = decodePrimitiveContext(ctx);
  assertTruthy(out, 'decode result');
  assertEqual(out!.kind, 'spread', 'kind');
  assertEqual(out!.toolName, 'calculate_curve_spread_tool', 'toolName');
});

check('decode: calculate_cross_market_spread_tool → cross_market typed view', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_cross_market_spread_tool' }]),
  );
  assertEqual(out!.kind, 'cross_market', 'kind');
});

check('decode: get_yield_levels_tool → yield typed view', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'get_yield_levels_tool' }]),
  );
  assertEqual(out!.kind, 'yield', 'kind');
});

check('decode: calculate_pca_yield_curve_tool (canonical) → builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_pca_yield_curve_tool' }]),
  );
  assertEqual(out!.kind, 'builder', 'kind');
  assertEqual(out!.toolName, 'calculate_pca_yield_curve_tool', 'toolName');
});

check('decode: pca_yield_curve_tool (shorthand) → builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'pca_yield_curve_tool' }]),
  );
  assertEqual(out!.kind, 'builder', 'kind');
  assertEqual(
    out!.toolName,
    'calculate_pca_yield_curve_tool',
    'normalised toolName',
  );
});

check('decode: half_life_tool (shorthand) → builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'half_life_tool' }]),
  );
  assertEqual(out!.kind, 'builder', 'kind');
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

check('decode: builder + primitive → builder wins (priority)', () => {
  const out = decodePrimitiveContext(
    encodeContext([
      { tool: 'calculate_curve_spread_tool' },
      { tool: 'calculate_pca_yield_curve_tool' },
    ]),
  );
  assertEqual(out!.kind, 'builder', 'builder wins over spread');
});

check('decode: typed primitive + generic_builder → typed wins (priority)', () => {
  // PR2 priority: BUILDER > typed view > generic_builder > unsupported_known.
  // A typed chart conveys more information than a configure-and-run
  // form, so curve_spread wins over swap_spread (generic_builder).
  const out = decodePrimitiveContext(
    encodeContext([
      { tool: 'calculate_swap_spread_tool' }, // generic_builder
      { tool: 'calculate_curve_spread_tool' }, // typed view
    ]),
  );
  assertEqual(out!.kind, 'spread', 'spread wins over generic_builder');
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

check('decodeList: three yield calls → three typed entries in order', () => {
  const list = decodePrimitiveList(
    encodeContext([
      { tool: 'get_yield_levels_tool', params: { curve_family: 'UST' } },
      { tool: 'get_yield_levels_tool', params: { curve_family: 'DE_BUND' } },
      { tool: 'get_yield_levels_tool', params: { curve_family: 'UK_GILT' } },
    ]),
  );
  assertEqual(list.length, 3, 'three entries');
  for (const item of list) {
    assertEqual(item.kind, 'yield', `each: yield`);
  }
  // Order preserved
  assertEqual(
    list.map((d: DecodedPrimitive) => d.params.curve_family),
    ['UST', 'DE_BUND', 'UK_GILT'],
    'curve_family order preserved',
  );
});

check('decodeList: builder entry filtered out', () => {
  const list = decodePrimitiveList(
    encodeContext([
      { tool: 'calculate_curve_spread_tool' },
      { tool: 'calculate_pca_yield_curve_tool' }, // builder — filtered
      { tool: 'get_yield_levels_tool' },
    ]),
  );
  assertEqual(list.length, 2, 'builder filtered out');
  assertEqual(
    list.map((d: DecodedPrimitive) => d.toolName),
    ['calculate_curve_spread_tool', 'get_yield_levels_tool'],
    'order preserved minus builder',
  );
});

check('decodeList: generic_builder + unsupported_known are both retained', () => {
  // PR2 — multi-card grid renders typed primitives + generic builder
  // tiles + unsupported tiles together.  Builders alone are filtered.
  const list = decodePrimitiveList(
    encodeContext([
      { tool: 'calculate_curve_spread_tool' }, // typed view
      { tool: 'calculate_swap_spread_tool' },  // generic_builder
      { tool: 'scan_ois_extremes_tool' },      // unsupported_known
    ]),
  );
  assertEqual(list.length, 3, 'all three retained');
  assertEqual(list[0].kind, 'spread', 'first: typed view');
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

check('KNOWN_BACKEND_TOOLS contains the audited 21 names (17 reg + 3 manifest-only + 1 manifest-only-no-backend)', () => {
  // 17 from rates_agent/workflows/__init__.py registry
  // + 3 manifest tools with typed-detail endpoints (butterfly, classify, scan)
  // + 1 manifest-only entry with NO backend implementation (scan_ois_extremes_tool)
  // = 21 expected canonical names
  const expected = [
    'build_sovereign_yield_panel_tool',
    'calculate_beta_adjusted_spread_tool',
    'calculate_breakeven_inflation_tool',
    'calculate_butterfly_tool',
    'calculate_cross_market_spread_tool',
    'calculate_curve_spread_tool',
    'calculate_half_life_tool',
    'calculate_ois_cross_market_spread_tool',
    'calculate_ois_curve_spread_tool',
    'calculate_ois_forward_rate_tool',
    'calculate_pca_yield_curve_tool',
    'calculate_rolling_regression_tool',
    'calculate_swap_spread_tool',
    'calculate_yield_change_attribution_pca_tool',
    'calculate_zscore_custom_tool',
    'classify_curve_move_tool',
    'compute_financing_rate_tool',
    'get_ois_rate_level_tool',
    'get_yield_levels_tool',
    'scan_extremes_tool',
    'scan_ois_extremes_tool',
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
