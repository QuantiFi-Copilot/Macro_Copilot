// ============================================================================
// src/modules/index.ts — central loader barrel.
// ----------------------------------------------------------------------------
// Stage 3 — populated with all 58 primitive module imports + entries
// in ``ALL_PRIMITIVE_MODULES``.  Each module's spec is a pure value
// (FM7); the loader has zero side effects.
//
// Adding a module = one import line + one array entry, alphabetised by
// tool_name.  The per-module round-trip test (``__tests__/module.spec.ts``
// in each module folder) + the loader-presence test
// (``src/lib/__tests__/loaderPresence.test.ts``) catch drift.
//
// Stage 3 reality
// ---------------
// All 58 modules ship with the MINIMUM-VIABLE shape — just the runtime-
// status tier.  Capability tiers + surface refs land in Stage 4a/4b/4c
// as the legacy surface code moves into each module folder.  The
// hybrid-derivation wiring in src/lib/toolNames.ts unions module-
// derived sets with the Stage 1 hand-authored entries; today the
// derived sets contribute the same tool names already in the hand-
// authored entries, so the resulting central registries are
// behaviour-identical to Stage 1.
// ============================================================================

import type {
  PrimitiveModuleSpec,
  WorkflowModuleSpec,
} from './types';

// ----------------------------------------------------------------------------
// Primitive module imports — alphabetised by tool_name.
// ----------------------------------------------------------------------------

// Stage 5 — smoke-test fixture (synthetic toolName starting with ``__``;
// excluded from cross-side parity check via the ``__``-prefix filter
// in ``tools/check_module_parity.py``).  Sorted first to keep the
// ASCII alphabetical order accurate.
import { MODULE as __smoke_test_tool } from './primitives/__smoke_test_tool/module';
import { MODULE as build_linker_panel_tool } from './primitives/build_linker_panel_tool/module';
import { MODULE as build_policy_futures_strip_panel_tool } from './primitives/build_policy_futures_strip_panel_tool/module';
import { MODULE as build_sovereign_yield_panel_tool } from './primitives/build_sovereign_yield_panel_tool/module';
import { MODULE as build_zcis_panel_tool } from './primitives/build_zcis_panel_tool/module';
import { MODULE as calculate_beta_adjusted_spread_tool } from './primitives/calculate_beta_adjusted_spread_tool/module';
import { MODULE as calculate_breakeven_butterfly_tool } from './primitives/calculate_breakeven_butterfly_tool/module';
import { MODULE as calculate_breakeven_curve_spread_tool } from './primitives/calculate_breakeven_curve_spread_tool/module';
import { MODULE as calculate_breakeven_inflation_simple_tool } from './primitives/calculate_breakeven_inflation_simple_tool/module';
import { MODULE as calculate_breakeven_inflation_tool } from './primitives/calculate_breakeven_inflation_tool/module';
import { MODULE as calculate_butterfly_tool } from './primitives/calculate_butterfly_tool/module';
import { MODULE as calculate_cpi_surprise_tool } from './primitives/calculate_cpi_surprise_tool/module';
import { MODULE as calculate_cross_country_breakeven_spread_simple_tool } from './primitives/calculate_cross_country_breakeven_spread_simple_tool/module';
import { MODULE as calculate_cross_country_real_yield_spread_simple_tool } from './primitives/calculate_cross_country_real_yield_spread_simple_tool/module';
import { MODULE as calculate_cross_market_inflation_swap_spread_tool } from './primitives/calculate_cross_market_inflation_swap_spread_tool/module';
import { MODULE as calculate_cross_market_spread_tool } from './primitives/calculate_cross_market_spread_tool/module';
import { MODULE as calculate_curve_spread_tool } from './primitives/calculate_curve_spread_tool/module';
import { MODULE as calculate_forward_breakeven_simple_tool } from './primitives/calculate_forward_breakeven_simple_tool/module';
import { MODULE as calculate_half_life_tool } from './primitives/calculate_half_life_tool/module';
import { MODULE as calculate_inflation_swap_butterfly_tool } from './primitives/calculate_inflation_swap_butterfly_tool/module';
import { MODULE as calculate_inflation_swap_curve_spread_tool } from './primitives/calculate_inflation_swap_curve_spread_tool/module';
import { MODULE as calculate_inflation_swap_forward_tool } from './primitives/calculate_inflation_swap_forward_tool/module';
import { MODULE as calculate_inflation_swap_rate_level_tool } from './primitives/calculate_inflation_swap_rate_level_tool/module';
import { MODULE as calculate_nfp_surprise_tool } from './primitives/calculate_nfp_surprise_tool/module';
import { MODULE as calculate_ois_butterfly_tool } from './primitives/calculate_ois_butterfly_tool/module';
import { MODULE as calculate_ois_cross_market_spread_tool } from './primitives/calculate_ois_cross_market_spread_tool/module';
import { MODULE as calculate_ois_curve_spread_tool } from './primitives/calculate_ois_curve_spread_tool/module';
import { MODULE as calculate_ois_forward_rate_tool } from './primitives/calculate_ois_forward_rate_tool/module';
import { MODULE as calculate_otr_ofr_spread_tool } from './primitives/calculate_otr_ofr_spread_tool/module';
import { MODULE as calculate_pca_yield_curve_tool } from './primitives/calculate_pca_yield_curve_tool/module';
import { MODULE as calculate_real_yield_butterfly_tool } from './primitives/calculate_real_yield_butterfly_tool/module';
import { MODULE as calculate_real_yield_curve_spread_tool } from './primitives/calculate_real_yield_curve_spread_tool/module';
import { MODULE as calculate_rolling_regression_tool } from './primitives/calculate_rolling_regression_tool/module';
import { MODULE as calculate_swap_breakeven_basis_simple_tool } from './primitives/calculate_swap_breakeven_basis_simple_tool/module';
import { MODULE as calculate_swap_spread_tool } from './primitives/calculate_swap_spread_tool/module';
import { MODULE as calculate_wirp_meeting_pricing_tool } from './primitives/calculate_wirp_meeting_pricing_tool/module';
import { MODULE as calculate_yield_change_attribution_pca_tool } from './primitives/calculate_yield_change_attribution_pca_tool/module';
import { MODULE as calculate_zscore_custom_tool } from './primitives/calculate_zscore_custom_tool/module';
import { MODULE as classify_curve_move_tool } from './primitives/classify_curve_move_tool/module';
import { MODULE as compute_financing_rate_tool } from './primitives/compute_financing_rate_tool/module';
import { MODULE as get_futures_price_level_tool } from './primitives/get_futures_price_level_tool/module';
import { MODULE as get_futures_volume_oi_tool } from './primitives/get_futures_volume_oi_tool/module';
import { MODULE as get_ois_rate_level_tool } from './primitives/get_ois_rate_level_tool/module';
import { MODULE as get_otr_history_tool } from './primitives/get_otr_history_tool/module';
import { MODULE as get_real_yield_level_tool } from './primitives/get_real_yield_level_tool/module';
import { MODULE as get_scan_policy_futures_extremes_tool } from './primitives/get_scan_policy_futures_extremes_tool/module';
import { MODULE as get_yield_levels_tool } from './primitives/get_yield_levels_tool/module';
import { MODULE as policy_futures_get_futures_butterfly_simple_tool } from './primitives/policy_futures_get_futures_butterfly_simple_tool/module';
import { MODULE as policy_futures_get_futures_calendar_spread_tool } from './primitives/policy_futures_get_futures_calendar_spread_tool/module';
import { MODULE as policy_futures_get_futures_cross_market_spread_tool } from './primitives/policy_futures_get_futures_cross_market_spread_tool/module';
import { MODULE as policy_futures_get_futures_pack_average_simple_tool } from './primitives/policy_futures_get_futures_pack_average_simple_tool/module';
import { MODULE as policy_futures_get_futures_strip_snapshot_tool } from './primitives/policy_futures_get_futures_strip_snapshot_tool/module';
import { MODULE as policy_futures_get_volume_open_interest_snapshot_tool } from './primitives/policy_futures_get_volume_open_interest_snapshot_tool/module';
import { MODULE as scan_bond_futures_extremes_tool } from './primitives/scan_bond_futures_extremes_tool/module';
import { MODULE as scan_extremes_tool } from './primitives/scan_extremes_tool/module';
import { MODULE as scan_inflation_linkers_extremes_tool } from './primitives/scan_inflation_linkers_extremes_tool/module';
import { MODULE as get_scan_inflation_swaps_extremes_tool } from './primitives/get_scan_inflation_swaps_extremes_tool/module';
import { MODULE as scan_ois_extremes_tool } from './primitives/scan_ois_extremes_tool/module';

// ----------------------------------------------------------------------------
// (no workflow module imports yet — Stage 7+ adds them here)
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
// Public exports — the derived registries read from these arrays.
// ----------------------------------------------------------------------------

/** Every primitive module the loader knows about, in stable
 *  alphabetical order.  Pure data — derived sets in
 *  ``src/lib/toolNames.ts`` union these spec-derived contributions
 *  with the Stage 1 hand-authored entries through the migration. */
export const ALL_PRIMITIVE_MODULES: ReadonlyArray<PrimitiveModuleSpec> = [
  __smoke_test_tool,
  build_linker_panel_tool,
  build_policy_futures_strip_panel_tool,
  build_sovereign_yield_panel_tool,
  build_zcis_panel_tool,
  calculate_beta_adjusted_spread_tool,
  calculate_breakeven_butterfly_tool,
  calculate_breakeven_curve_spread_tool,
  calculate_breakeven_inflation_simple_tool,
  calculate_breakeven_inflation_tool,
  calculate_butterfly_tool,
  calculate_cpi_surprise_tool,
  calculate_cross_country_breakeven_spread_simple_tool,
  calculate_cross_country_real_yield_spread_simple_tool,
  calculate_cross_market_inflation_swap_spread_tool,
  calculate_cross_market_spread_tool,
  calculate_curve_spread_tool,
  calculate_forward_breakeven_simple_tool,
  calculate_half_life_tool,
  calculate_inflation_swap_butterfly_tool,
  calculate_inflation_swap_curve_spread_tool,
  calculate_inflation_swap_forward_tool,
  calculate_inflation_swap_rate_level_tool,
  calculate_nfp_surprise_tool,
  calculate_ois_butterfly_tool,
  calculate_ois_cross_market_spread_tool,
  calculate_ois_curve_spread_tool,
  calculate_ois_forward_rate_tool,
  calculate_otr_ofr_spread_tool,
  calculate_pca_yield_curve_tool,
  calculate_real_yield_butterfly_tool,
  calculate_real_yield_curve_spread_tool,
  calculate_rolling_regression_tool,
  calculate_swap_breakeven_basis_simple_tool,
  calculate_swap_spread_tool,
  calculate_wirp_meeting_pricing_tool,
  calculate_yield_change_attribution_pca_tool,
  calculate_zscore_custom_tool,
  classify_curve_move_tool,
  compute_financing_rate_tool,
  get_futures_price_level_tool,
  get_futures_volume_oi_tool,
  get_ois_rate_level_tool,
  get_otr_history_tool,
  get_real_yield_level_tool,
  get_scan_policy_futures_extremes_tool,
  get_yield_levels_tool,
  policy_futures_get_futures_butterfly_simple_tool,
  policy_futures_get_futures_calendar_spread_tool,
  policy_futures_get_futures_cross_market_spread_tool,
  policy_futures_get_futures_pack_average_simple_tool,
  policy_futures_get_futures_strip_snapshot_tool,
  policy_futures_get_volume_open_interest_snapshot_tool,
  scan_bond_futures_extremes_tool,
  scan_extremes_tool,
  scan_inflation_linkers_extremes_tool,
  get_scan_inflation_swaps_extremes_tool,
  scan_ois_extremes_tool,
];

/** Every workflow module the loader knows about, in stable
 *  alphabetical order.  Stage 7+ populates this. */
export const ALL_WORKFLOW_MODULES: ReadonlyArray<WorkflowModuleSpec> = [
  // (no entries yet — Stage 7+ adds workflow modules here)
];

// ----------------------------------------------------------------------------
// Lookup helpers.
// ----------------------------------------------------------------------------

/** Resolve a primitive module by its backend-canonical ``tool_name``.
 *  Returns ``undefined`` when no module declares that tool.  Callers
 *  MUST normalise manifest shorthand BEFORE calling — use
 *  ``normalizeToolName`` from ``@/lib/toolNames``. */
export function getPrimitiveModule(
  toolName: string,
): PrimitiveModuleSpec | undefined {
  return ALL_PRIMITIVE_MODULES.find((m) => m.toolName === toolName);
}

/** Resolve a workflow module by its backend-canonical ``template_id``. */
export function getWorkflowModule(
  templateId: string,
): WorkflowModuleSpec | undefined {
  return ALL_WORKFLOW_MODULES.find((m) => m.templateId === templateId);
}

// ----------------------------------------------------------------------------
// Type re-exports.
// ----------------------------------------------------------------------------

export type {
  PrimitiveModuleSpec,
  WorkflowModuleSpec,
  SurfaceTier,
  RuntimeStatusTier,
  CapabilityTier,
  ModuleSurfaceBaseProps,
  BuildSurfaceProps,
  MonitorWidgetProps,
  AskCardProps,
  PreviewWidgetProps,
  UnsupportedKnownReason,
} from './types';
export {
  ALL_SURFACE_TIERS,
  RUNTIME_STATUS_TIERS,
  CAPABILITY_TIERS,
  isRuntimeStatusTier,
  isCapabilityTier,
} from './types';
