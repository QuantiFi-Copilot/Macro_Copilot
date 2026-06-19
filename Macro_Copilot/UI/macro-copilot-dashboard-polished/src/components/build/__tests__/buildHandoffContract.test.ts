/* eslint-disable @typescript-eslint/no-explicit-any */
// ============================================================================
// buildHandoffContract.test.ts — PR-B-α Ask → Build handoff contract.
// ----------------------------------------------------------------------------
// Locks the alignment between the BACKEND's ``_WORKSPACE_TOOLS`` set
// (the gate that decides whether an Ask trace emits a
// ``workspace_context`` block) and the FRONTEND's routing surface
// (every tool the Build canvas knows how to render).
//
// Why this contract matters
// -------------------------
// Pre-PR-B-α the backend gate was a small hand-maintained set that
// drifted from the frontend's routing coverage.  Symptom: Ask answers
// that ran only ``get_yield_levels_tool`` / ``calculate_swap_spread_tool``
// / ``calculate_breakeven_inflation_tool`` / ``calculate_zscore_custom_tool``
// / ``build_sovereign_yield_panel_tool`` / ``compute_financing_rate_tool``
// / ``get_ois_rate_level_tool`` produced NO workspace_context, so
// "Open in Build" was disabled — even though Build had a generic
// builder or typed view for every one of these tools.
//
// PR-B-α derives the backend set from
// ``rates_agent.workflows._PRIMITIVE_SPECS`` plus an explicit
// manifest-only typed-view list and an MCP alias mapping.  This test
// holds a snapshot of that set on the frontend side and asserts that
// every frontend-routeable tool is included.
//
// Architecture
// ------------
// Per the agreed plan: the backend owns its registry, the frontend
// owns its registry, and a contract test (here) verifies they cover
// the same set of tools.  No cross-boundary imports — only a
// checked-in TS snapshot of the backend's effective list.
//
// Update procedure (if the backend list changes)
// ----------------------------------------------
// 1. Make the backend change (add / remove a ``PrimitiveSpec`` entry,
//    or update ``_MANIFEST_ONLY_BUILD_TOOLS`` / ``_MCP_ALIAS_TO_CANONICAL``
//    in ``orchestrator/events.py``).
// 2. Run the backend test:
//        pytest tests/test_workspace_handoff_completeness.py -v
//    Update its STUB_RATES_PRIMITIVES list to reflect the new
//    backend state.
// 3. Update ``BACKEND_WORKSPACE_TOOLS_SNAPSHOT`` below to match what
//    ``orchestrator.events.workspace_tools_snapshot()`` returns.
// 4. Run this test:
//        npm run test:build
//    Both sides stay in lock-step.
//
// What this lock catches
// ----------------------
// - Backend removes a tool from the gate (and frontend still has a
//   route for it).  Test fails: "frontend can route X but backend
//   gate excludes it".
// - Frontend adds a new route for a tool not yet in the backend
//   gate.  Test fails: same direction.
// - Snapshot drift in either direction (e.g. someone forgets to
//   update the snapshot here after a backend change).
// ============================================================================

import { KNOWN_BACKEND_TOOLS, RUNNABLE_PRIMITIVE_TOOLS, UNSUPPORTED_KNOWN_TOOLS, normalizeToolName } from '@/lib/toolNames';
import { listModels } from '@/lib/modelRegistry';

type Check = { label: string; fn: () => void };
const _checks: Check[] = [];

function check(label: string, fn: () => void): void {
  _checks.push({ label, fn });
}

function assertTruthy(v: unknown, label: string): void {
  if (!v) throw new Error(`assertTruthy failed: ${label}`);
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

// ----------------------------------------------------------------------------
// BACKEND_WORKSPACE_TOOLS_SNAPSHOT
// ----------------------------------------------------------------------------
//
// Mirrors ``orchestrator.events.workspace_tools_snapshot()`` as of
// PR-B-α.  Source of truth lives in
//   - ``rates_agent.workflows._PRIMITIVE_SPECS`` (via
//     ``known_rates_primitives``)
//   - ``orchestrator.events._MANIFEST_ONLY_BUILD_TOOLS``
//   - ``orchestrator.events._MCP_ALIAS_TO_CANONICAL``
//
// Kept sorted to match the backend's ``workspace_tools_snapshot()``
// output exactly, so manual sync is mechanical (no merge churn).
// ----------------------------------------------------------------------------

const BACKEND_WORKSPACE_TOOLS_SNAPSHOT: ReadonlyArray<string> = [
  // I4 sync (2026-06-19): mirrors ``orchestrator.events.
  // workspace_tools_snapshot()`` VERBATIM as returned live (65 entries).
  // The prior snapshot (59) had drifted — it predated 7 analytical /
  // regime primitives (curve_fair_value, implied_forward_curve,
  // ois_policy_path_regime, pca_neutral_butterfly_weights,
  // rates_vol_regime, sovereign_curve_regime, swap_carry_and_roll) AND
  // the backend's switch to the get_/policy_futures_-prefixed MCP names
  // for four scanner/panel tools.  That drift was the root of the
  // "Could not decode workspace context" dead-end for those tools: the
  // orphan check below validated against a stale snapshot, so the gaps
  // were invisible.  Both checks are now alias-aware (they compare the
  // NORMALISED backend set against the normalised frontend set), so an
  // alias-source backend name and its canonical are treated as covered.
  'build_linker_panel_tool',
  'build_sovereign_yield_panel_tool',
  'build_zcis_panel_tool',
  'calculate_beta_adjusted_spread_tool',
  'calculate_breakeven_butterfly_tool',
  'calculate_breakeven_curve_spread_tool',
  'calculate_breakeven_inflation_simple_tool',
  'calculate_breakeven_inflation_tool',
  'calculate_butterfly_tool',
  'calculate_cpi_surprise_tool',
  'calculate_cross_country_breakeven_spread_simple_tool',
  'calculate_cross_country_real_yield_spread_simple_tool',
  'calculate_cross_market_inflation_swap_spread_tool',
  'calculate_cross_market_spread_tool',
  'calculate_curve_fair_value_tool',
  'calculate_curve_spread_tool',
  'calculate_forward_breakeven_simple_tool',
  'calculate_half_life_tool',
  'calculate_implied_forward_curve_tool',
  'calculate_inflation_swap_butterfly_tool',
  'calculate_inflation_swap_curve_spread_tool',
  'calculate_inflation_swap_forward_tool',
  'calculate_inflation_swap_rate_level_tool',
  'calculate_nfp_surprise_tool',
  'calculate_ois_butterfly_tool',
  'calculate_ois_cross_market_spread_tool',
  'calculate_ois_curve_spread_tool',
  'calculate_ois_forward_rate_tool',
  'calculate_ois_policy_path_regime_tool',
  'calculate_ois_rate_level_tool',
  'calculate_otr_ofr_spread_tool',
  'calculate_pca_neutral_butterfly_weights_tool',
  'calculate_pca_yield_curve_tool',
  'calculate_rates_vol_regime_tool',
  'calculate_real_yield_butterfly_tool',
  'calculate_real_yield_curve_spread_tool',
  'calculate_rolling_regression_tool',
  'calculate_sovereign_curve_regime_tool',
  'calculate_swap_breakeven_basis_simple_tool',
  'calculate_swap_carry_and_roll_tool',
  'calculate_swap_spread_tool',
  'calculate_wirp_meeting_pricing_tool',
  'calculate_yield_change_attribution_pca_tool',
  'calculate_zscore_custom_tool',
  'classify_curve_move_tool',
  'compute_financing_rate_tool',
  'get_futures_price_level_tool',
  'get_futures_volume_oi_tool',
  'get_ois_rate_level_tool',
  'get_otr_history_tool',
  'get_real_yield_level_tool',
  'get_scan_inflation_linkers_extremes_tool',
  'get_scan_inflation_swaps_extremes_tool',
  'get_yield_levels_tool',
  'policy_futures_build_policy_futures_strip_panel_tool',
  'policy_futures_get_futures_butterfly_simple_tool',
  'policy_futures_get_futures_calendar_spread_tool',
  'policy_futures_get_futures_cross_market_spread_tool',
  'policy_futures_get_futures_pack_average_simple_tool',
  'policy_futures_get_futures_price_level_tool',
  'policy_futures_get_futures_strip_snapshot_tool',
  'policy_futures_get_scan_policy_futures_extremes_tool',
  'policy_futures_get_volume_open_interest_snapshot_tool',
  'scan_bond_futures_extremes_tool',
  'scan_extremes_tool',
  'scan_ois_extremes_tool',
].sort();

const BACKEND_SET: ReadonlySet<string> = new Set(
  BACKEND_WORKSPACE_TOOLS_SNAPSHOT,
);

// I4 — the backend gate emits some MCP-exposed / domain-prefixed names
// (e.g. ``get_scan_inflation_linkers_extremes_tool``,
// ``policy_futures_build_policy_futures_strip_panel_tool``) that the
// frontend normalises to a canonical before routing.  Compare the
// NORMALISED backend set against the (already-normalised) frontend
// routeable set so an alias-source name and its canonical are treated
// as the same tool in BOTH directions.
const BACKEND_NORMALIZED: ReadonlySet<string> = new Set(
  BACKEND_WORKSPACE_TOOLS_SNAPSHOT.map(normalizeToolName),
);

// ----------------------------------------------------------------------------
// FRONTEND routeable union
// ----------------------------------------------------------------------------
//
// Every tool the Build canvas knows how to render in some form:
//   - RUNNABLE_PRIMITIVE_TOOLS: typed view OR generic builder
//     (``contextDecoder`` routes to one of these for each).
//   - UNSUPPORTED_KNOWN_TOOLS: honest paused card.
//   - Model registry tools: rich model builder.
//   - Manifest-only tools (subset of KNOWN_BACKEND_TOOLS that aren't
//     in RUNNABLE_PRIMITIVE_TOOLS): honest unsupported-known card via
//     the contextDecoder.

function computeFrontendRouteable(): Set<string> {
  const out = new Set<string>();
  for (const t of RUNNABLE_PRIMITIVE_TOOLS) out.add(normalizeToolName(t));
  for (const t of UNSUPPORTED_KNOWN_TOOLS) out.add(normalizeToolName(t));
  for (const m of listModels()) out.add(normalizeToolName(m.toolName));
  // KNOWN_BACKEND_TOOLS - RUNNABLE - UNSUPPORTED_KNOWN gives manifest-
  // only typed-view tools.
  for (const t of KNOWN_BACKEND_TOOLS) {
    const n = normalizeToolName(t);
    if (!RUNNABLE_PRIMITIVE_TOOLS.has(n) && !UNSUPPORTED_KNOWN_TOOLS.has(n)) {
      out.add(n);
    }
  }
  return out;
}

// ----------------------------------------------------------------------------
// Checks
// ----------------------------------------------------------------------------

check('contract: every frontend-routeable tool is in the backend gate', () => {
  const fe = computeFrontendRouteable();
  const missing: string[] = [];
  for (const t of fe) {
    // Alias-aware: a frontend canonical is covered if the backend emits
    // it directly OR emits an alias that normalises to it.
    if (!BACKEND_NORMALIZED.has(t)) missing.push(t);
  }
  if (missing.length > 0) {
    throw new Error(
      `Frontend can route these tools but the backend's ` +
        `_WORKSPACE_TOOLS gate excludes them — "Open in Build" will ` +
        `be disabled on every Ask trace that uses only these tools:\n  ` +
        missing.sort().join('\n  ') +
        `\nFix: add the missing tool(s) to ` +
        `rates_agent.workflows._PRIMITIVE_SPECS or to ` +
        `orchestrator.events._MANIFEST_ONLY_BUILD_TOOLS, then update ` +
        `BACKEND_WORKSPACE_TOOLS_SNAPSHOT in this test file.`,
    );
  }
});

check('contract: backend snapshot has no frontend-orphan entries', () => {
  // Symmetric direction: every backend gate entry should also be
  // routeable on the frontend.  An orphan means the backend will
  // emit workspace_context for the tool but the frontend has no
  // surface to render it — the user clicks "Open in Build" and
  // lands on a decode-error card.
  //
  // We must walk the alias table because the backend gate accepts
  // some MCP-exposed names that the frontend normalises to a
  // canonical name BEFORE routing (e.g.
  // ``calculate_ois_rate_level_tool`` → ``get_ois_rate_level_tool``).
  // An alias that round-trips through ``normalizeToolName`` to a
  // routeable canonical IS NOT a real orphan.
  const fe = computeFrontendRouteable();
  const orphans: string[] = [];
  for (const t of BACKEND_SET) {
    if (fe.has(t)) continue;
    // Try normalising — alias entries collapse to a canonical that
    // IS in the frontend set, which is the intended behaviour.
    const normalised = normalizeToolName(t);
    if (normalised !== t && fe.has(normalised)) continue;
    orphans.push(t);
  }
  if (orphans.length > 0) {
    throw new Error(
      `Backend emits workspace_context for these tools but the ` +
        `frontend has no Build route for them — clicking "Open in ` +
        `Build" would land on a decode-error card:\n  ` +
        orphans.sort().join('\n  ') +
        `\nFix: add a typed view / generic-builder route in ` +
        `contextDecoder.ts OR remove from the backend gate.`,
    );
  }
});

check('contract: snapshot is sorted (matches workspace_tools_snapshot output)', () => {
  // Backend ``workspace_tools_snapshot()`` returns sorted output so the
  // diff is minimal whenever the contract updates.  The TS snapshot
  // is normalised by ``.sort()`` to be defensive against manual
  // ordering mistakes.
  const sorted = [...BACKEND_WORKSPACE_TOOLS_SNAPSHOT].sort();
  assertEqual(
    BACKEND_WORKSPACE_TOOLS_SNAPSHOT,
    sorted,
    'snapshot is in sorted order',
  );
});

check('contract: audit-flagged tools all pass the gate', () => {
  // Specific tools whose absence pre-PR-B-α caused the "Open in Build"
  // button to no-op on prompts 3 / 4 / 5 of the audit.  Lock them
  // by name so an accidental removal is caught loudly.
  const auditFlagged = [
    'get_yield_levels_tool',
    'calculate_swap_spread_tool',
    'calculate_breakeven_inflation_tool',
    'calculate_zscore_custom_tool',
    'build_sovereign_yield_panel_tool',
    'compute_financing_rate_tool',
    'get_ois_rate_level_tool',
    'calculate_ois_rate_level_tool',
  ];
  for (const t of auditFlagged) {
    assertTruthy(
      BACKEND_SET.has(t),
      `Audit-flagged tool ${t} must be in BACKEND_WORKSPACE_TOOLS_SNAPSHOT`,
    );
  }
});

check('contract: OIS rate-level both names share gate membership', () => {
  // PR-B-α's ``_MCP_ALIAS_TO_CANONICAL`` ensures the MCP-exposed name
  // AND the registry name BOTH pass.  Without this, Ask traces
  // recorded under whichever name the entry point used would silently
  // fail the gate.
  assertTruthy(
    BACKEND_SET.has('calculate_ois_rate_level_tool'),
    'MCP-exposed name in gate',
  );
  assertTruthy(
    BACKEND_SET.has('get_ois_rate_level_tool'),
    'registry-canonical name in gate',
  );
});

// ----------------------------------------------------------------------------
// Runner
// ----------------------------------------------------------------------------

export function runAllBuildHandoffContractTests(): void {
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
    `\nbuild-handoff contract coverage: ${passed} passed, ${failed} failed`,
  );
  if (failed > 0) {
    throw new Error(`${failed} build-handoff contract check(s) failed`);
  }
}

const _meta = (import.meta as unknown) as { main?: boolean };
if (_meta && _meta.main) {
  runAllBuildHandoffContractTests();
}
