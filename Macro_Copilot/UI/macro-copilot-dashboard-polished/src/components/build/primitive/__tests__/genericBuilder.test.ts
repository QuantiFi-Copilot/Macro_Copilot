/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// genericBuilder.test.ts — PR2 generic-builder + paramHint-inference assertions.
// ----------------------------------------------------------------------------
// Sibling of ``routingCoverage.test.ts``.  Covers the PR2-specific
// surface area:
//
//   1. ``RUNNABLE_PRIMITIVE_TOOLS`` registry membership.
//   2. ``isRunnablePrimitive`` resolves manifest shorthand + the verb
//      mismatch.
//   3. Decoder emits ``generic_builder`` for every runnable primitive
//      without a typed view AND without a model-registry entry.
//   4. Decoder still emits the right ``builder`` / typed-view / paused
//      variant for the surrounding registry rows.
//   5. ``inferFieldControl`` returns sensible controls for the shared
//      rates vocabulary (curve_family / tenor / lookback_days / etc.).
//
// Same ``check`` shim + esbuild-bundle execution path as the PR1
// routingCoverage tests; the file is tsc-checked under the app
// tsconfig and exports a runner the harness can call directly.
// ============================================================================

import {
  decodePrimitiveContext,
  decodePrimitiveList,
} from '../contextDecoder';
import {
  RUNNABLE_PRIMITIVE_TOOLS,
  isRunnablePrimitive,
  normalizeToolName,
} from '@/lib/toolNames';
import { inferFieldControl, paramHintFor } from '@/lib/modelRegistry';

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
// RUNNABLE_PRIMITIVE_TOOLS — closed-set sanity
// ----------------------------------------------------------------------------

check('RUNNABLE_PRIMITIVE_TOOLS: contains exactly the 52 _PRIMITIVE_SPECS keys (Stage 1)', () => {
  // Stage 1 — RUNNABLE_PRIMITIVE_TOOLS expands from 34 to 52 to
  // mirror backend ground truth on `build` today.
  // Source of truth: grep -E "^\\s*tool_name=" rates_agent/workflows/__init__.py
  // produces the same 52 entries (every key in _PRIMITIVE_SPECS).
  //
  // The size assertion catches both directions:
  //   * Adding a primitive to the backend without updating this set
  //     → assertion fails because the actual size exceeds expected.
  //   * Removing a primitive from the frontend set without backend
  //     coordination → some `for (const t of expected)` lookup fails.
  const expected = [
    // Sovereign-bond domain (12)
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
    // OIS domain (5)
    'calculate_ois_cross_market_spread_tool',
    'calculate_ois_curve_spread_tool',
    'calculate_ois_forward_rate_tool',
    'compute_financing_rate_tool',
    'get_ois_rate_level_tool',
    // Factory-ported (ADR 0013) — 1 OIS + 3 bond_futures + 9 policy_futures + 2 inflation_indexed + 2 inflation_swaps = 17
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
    // Stage 1 net-new runnable primitives (18) — Phase-3 + PR #177
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
    // I4 (2026-06-19) — analytical / regime primitives that ship in
    // backend ``_PRIMITIVE_SPECS`` (verified runnable: each has a
    // ``POST /tools/{name}/run`` endpoint) but had no frontend module
    // and were absent from this registry, so an Ask→Build hand-off for
    // any of them decode-nulled into "Could not decode workspace
    // context".  Added to RUNNABLE so they route through the
    // schema-driven GenericPrimitiveBuilder.
    'calculate_curve_fair_value_tool',
    'calculate_implied_forward_curve_tool',
    'calculate_ois_policy_path_regime_tool',
    'calculate_pca_neutral_butterfly_weights_tool',
    'calculate_rates_vol_regime_tool',
    'calculate_sovereign_curve_regime_tool',
    'calculate_swap_carry_and_roll_tool',
  ];
  assertEqual(
    RUNNABLE_PRIMITIVE_TOOLS.size,
    expected.length,
    `set size (got ${RUNNABLE_PRIMITIVE_TOOLS.size}, expected ${expected.length})`,
  );
  for (const t of expected) {
    assertTruthy(RUNNABLE_PRIMITIVE_TOOLS.has(t), `missing: ${t}`);
  }
});

check('isRunnablePrimitive: typed-view tools are runnable', () => {
  // All seven typed-view tools live in ``_PRIMITIVE_SPECS`` so the
  // decoder's "typed view" step beats the "generic builder" step
  // for them.  Confirming they're runnable rules out a misalignment.
  for (const t of [
    'calculate_curve_spread_tool',
    'calculate_cross_market_spread_tool',
    'get_yield_levels_tool',
  ]) {
    assertTruthy(isRunnablePrimitive(t), `runnable: ${t}`);
  }
});

check('isRunnablePrimitive: resolves manifest shorthand', () => {
  assertTruthy(
    isRunnablePrimitive('half_life_tool'),
    'half_life shorthand is runnable',
  );
  assertTruthy(
    isRunnablePrimitive('pca_yield_curve_tool'),
    'pca shorthand is runnable',
  );
});

check('isRunnablePrimitive: verb-mismatch alias resolves', () => {
  // ``calculate_ois_rate_level_tool`` (manifest) → ``get_ois_rate_level_tool``
  // (backend).  Both forms should report runnable.
  assertTruthy(
    isRunnablePrimitive('calculate_ois_rate_level_tool'),
    'calculate_ois_rate_level shorthand is runnable',
  );
  assertTruthy(
    isRunnablePrimitive('get_ois_rate_level_tool'),
    'get_ois_rate_level canonical is runnable',
  );
});

check('isRunnablePrimitive: scan_ois_extremes_tool is NOT runnable', () => {
  // Manifest-only entry — no PrimitiveSpec on the backend, so
  // attempting to POST /tools/scan_ois_extremes_tool/run would 500.
  // This is the only ``KNOWN_BACKEND_TOOLS`` entry that should
  // surface the paused card after PR2.
  assertEqual(
    isRunnablePrimitive('scan_ois_extremes_tool'),
    false,
    'scan_ois_extremes_tool is paused',
  );
});

check('isRunnablePrimitive: manifest-only typed-view tools are NOT runnable', () => {
  // ``calculate_butterfly_tool``, ``classify_curve_move_tool``,
  // ``scan_extremes_tool`` ship typed-detail GET endpoints on the
  // rates API but aren't registered in ``_PRIMITIVE_SPECS``.  The
  // decoder routes them through their typed views; ``isRunnablePrimitive``
  // returns false so we never accidentally route them through
  // ``POST /tools/{name}/run``.
  for (const t of [
    'calculate_butterfly_tool',
    'classify_curve_move_tool',
    'scan_extremes_tool',
  ]) {
    assertEqual(isRunnablePrimitive(t), false, `not runnable via /run: ${t}`);
  }
});

// ----------------------------------------------------------------------------
// Decoder — every runnable primitive without a typed view → generic_builder
// ----------------------------------------------------------------------------

check('decode: calculate_swap_spread_tool → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_swap_spread_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
  assertEqual(out!.toolName, 'calculate_swap_spread_tool', 'toolName');
});

check('decode: calculate_ois_curve_spread_tool → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_ois_curve_spread_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('decode: calculate_ois_cross_market_spread_tool → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_ois_cross_market_spread_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('decode: calculate_ois_forward_rate_tool → generic_builder (replaces placeholder)', () => {
  // PR2 — previously this routed to the no-op ``forward`` typed view.
  // Now it routes to the generic builder which can actually configure
  // + run the primitive against the backend.
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_ois_forward_rate_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('decode: get_ois_rate_level_tool → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'get_ois_rate_level_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('decode: calculate_ois_rate_level_tool (verb-mismatch shorthand) → generic_builder + normalised', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_ois_rate_level_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
  assertEqual(out!.toolName, 'get_ois_rate_level_tool', 'normalised toolName');
});

check('decode: calculate_breakeven_inflation_tool → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_breakeven_inflation_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('decode: build_sovereign_yield_panel_tool → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'build_sovereign_yield_panel_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('decode: compute_financing_rate_tool → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'compute_financing_rate_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('decode: calculate_zscore_custom_tool → generic_builder', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'calculate_zscore_custom_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
});

check('decode: zscore_custom_tool (shorthand) → generic_builder + normalised', () => {
  const out = decodePrimitiveContext(
    encodeContext([{ tool: 'zscore_custom_tool' }]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
  assertEqual(
    out!.toolName,
    'calculate_zscore_custom_tool',
    'normalised toolName',
  );
});

check('decode: params dict is forwarded into the generic_builder variant', () => {
  // The schema-driven builder reads the URL params dict on mount; an
  // Ask hand-off that carries ``{curve_family: USD_SOFR_OIS, tenor: 5Y}``
  // should land in the form pre-filled.
  const out = decodePrimitiveContext(
    encodeContext([
      {
        tool: 'calculate_ois_curve_spread_tool',
        params: { curve_family: 'USD_SOFR_OIS', short_tenor: '2Y', long_tenor: '5Y' },
      },
    ]),
  );
  assertEqual(out!.kind, 'generic_builder', 'kind');
  assertEqual(
    out!.params,
    { curve_family: 'USD_SOFR_OIS', short_tenor: '2Y', long_tenor: '5Y' },
    'params forwarded verbatim',
  );
});

// ----------------------------------------------------------------------------
// Decoder priority + model-builder safety
// ----------------------------------------------------------------------------

check('decode: migrated rich-models route module-first, not builder (G-3.2)', () => {
  // Consolidation G-3.2: the five rich-models are dual-view modules —
  // ``modelMetadata`` is retired, so they decode 'generic_builder' and
  // VirtualPrimitiveCanvas's module-first dispatch mounts their
  // ``surfaces.buildExtended``.  The decode kind no longer carries the
  // bespoke surface; the module spec does.
  for (const t of [
    'calculate_pca_yield_curve_tool',
    'calculate_rolling_regression_tool',
    'calculate_yield_change_attribution_pca_tool',
    'calculate_half_life_tool',
    'calculate_beta_adjusted_spread_tool',
  ]) {
    const out = decodePrimitiveContext(encodeContext([{ tool: t }]));
    assertEqual(out!.kind, 'generic_builder', `${t}: kind`);
    assertEqual(out!.toolName, normalizeToolName(t), `${t}: toolName`);
  }
});

check('decodeList: generic_builder entries are retained in order', () => {
  const list = decodePrimitiveList(
    encodeContext([
      { tool: 'calculate_swap_spread_tool' },               // generic_builder
      { tool: 'classify_curve_move_tool' },                 // typed view
      { tool: 'calculate_ois_curve_spread_tool' },          // generic_builder
    ]),
  );
  assertEqual(list.length, 3, 'three entries');
  assertEqual(list[0].kind, 'generic_builder', '0: generic_builder');
  assertEqual(list[1].kind, 'workflow_incompatible', '1: migrated classifier (dual-view module)');
  assertEqual(list[2].kind, 'generic_builder', '2: generic_builder');
});

// ----------------------------------------------------------------------------
// inferFieldControl — schema-driven field heuristic
// ----------------------------------------------------------------------------

check('inferFieldControl: curve family aliases all resolve to curve_family', () => {
  for (const name of [
    'curve_family',
    'curve_family_1',
    'curve_family_2',
    'sovereign_curve_family',
    'ois_curve_family',
    'nominal_curve_family',
    'real_curve_family',
    'proxy_curve',
  ]) {
    assertEqual(inferFieldControl(name).control, 'curve_family', `${name}: control`);
  }
});

check('inferFieldControl: tenor aliases all resolve to tenor', () => {
  for (const name of [
    'tenor',
    'short_tenor',
    'long_tenor',
    'belly_tenor',
    'target_tenor',
    'front_tenor',
    'back_tenor',
    'start_tenor',
    'end_tenor',
    'forward_start',
    'forward_length',
  ]) {
    assertEqual(inferFieldControl(name).control, 'tenor', `${name}: control`);
  }
});

check('inferFieldControl: lookback aliases all resolve to lookback_slider', () => {
  for (const name of [
    'lookback_days',
    'rolling_window_days',
    'z_score_window_days',
    'regression_window_days',
  ]) {
    assertEqual(
      inferFieldControl(name).control,
      'lookback_slider',
      `${name}: control`,
    );
  }
});

check('inferFieldControl: date aliases resolve to date control', () => {
  for (const name of [
    'start_date',
    'end_date',
    'as_of_date',
    'prior_date',
  ]) {
    assertEqual(inferFieldControl(name).control, 'date', `${name}: control`);
  }
});

check('inferFieldControl: unknown field name → auto', () => {
  assertEqual(
    inferFieldControl('something_random').control,
    'auto',
    'unknown name',
  );
});

// ----------------------------------------------------------------------------
// PR5 — Bloomberg observation-field detection
// ----------------------------------------------------------------------------
//
// Pre-PR5 the inferer recognised ``field_name`` only as a sort
// priority — there was no ``field_name`` ControlKind, so the bare
// ``field_name`` field rendered as plain text and the canonical
// prefixed variants (``sovereign_field_name`` / ``ois_field_name`` /
// ``nominal_field_name`` / ``real_field_name``) fell through to
// ``auto`` → free-text input.  Users could type a typo
// (``YDL_YTM_MID``) and only learn about it from a backend error.
// PR5 routes every ``*_field_name`` pattern to the new
// ``field_name`` ControlKind which renders the canonical
// Bloomberg-mnemonic dropdown.

check('inferFieldControl: bare field_name → field_name', () => {
  assertEqual(
    inferFieldControl('field_name').control,
    'field_name',
    'bare field_name',
  );
});

check('inferFieldControl: prefixed *_field_name → field_name', () => {
  for (const name of [
    'sovereign_field_name',
    'ois_field_name',
    'nominal_field_name',
    'real_field_name',
    'target_field_name',
    'regressor_field_name',
  ]) {
    assertEqual(
      inferFieldControl(name).control,
      'field_name',
      `${name}: control`,
    );
  }
});

check('inferFieldControl: field_name suffix collisions stay auto', () => {
  // We deliberately match only ``_field_name`` (with the underscore
  // boundary).  ``output_field`` and ``output_field_name_lock`` are
  // unrelated — they must NOT route to ``field_name``.
  for (const name of [
    'output_field',
    'output_field_name_lock',
    'fieldname', // no underscore
  ]) {
    assertEqual(
      inferFieldControl(name).control === 'field_name',
      false,
      `${name}: not field_name`,
    );
  }
});

check('paramHintFor: ois primitive field_name slot → field_name', () => {
  // End-to-end via paramHintFor: the OIS curve-spread primitive has
  // no model-registry entry, so the inference must drive the dropdown
  // for ``ois_field_name`` / ``sovereign_field_name`` parameters.
  for (const tool of [
    'calculate_ois_curve_spread_tool',
    'calculate_swap_spread_tool',
  ]) {
    const hint = paramHintFor(tool, 'ois_field_name');
    assertEqual(hint.control, 'field_name', `${tool}: ois_field_name`);
    const sov = paramHintFor(tool, 'sovereign_field_name');
    assertEqual(sov.control, 'field_name', `${tool}: sovereign_field_name`);
  }
});

check('paramHintFor: registered hint overrides inference', () => {
  // The rolling-regression model registers ``target_spec: series_spec``.
  // Without the explicit registration the field name has no canonical
  // mapping, but the registry hint must beat ``inferFieldControl`` for
  // every model tool.  Sanity check the override priority.
  const hint = paramHintFor(
    'calculate_rolling_regression_tool',
    'target_spec',
  );
  assertEqual(hint.control, 'series_spec', 'rolling regression target_spec');
});

check('paramHintFor: unregistered tool falls through to inference', () => {
  // Swap-spread is runnable but has no model-registry entry.  The
  // ``sovereign_curve_family`` field should be inferred as a
  // curve_family control via the new heuristic.
  const hint = paramHintFor(
    'calculate_swap_spread_tool',
    'sovereign_curve_family',
  );
  assertEqual(hint.control, 'curve_family', 'swap_spread sovereign_curve_family');
});

check('paramHintFor: unregistered tool, unknown field → auto', () => {
  const hint = paramHintFor('calculate_swap_spread_tool', 'something_unknown');
  assertEqual(hint.control, 'auto', 'falls through to auto');
});

// ----------------------------------------------------------------------------
// Runner — same shim as routingCoverage.test.ts.
// ----------------------------------------------------------------------------

export function runAllGenericBuilderTests(): void {
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
  console.log(`\ngeneric-builder coverage: ${passed} passed, ${failed} failed`);
  if (failed > 0) {
    throw new Error(`${failed} generic-builder check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllGenericBuilderTests();
}
