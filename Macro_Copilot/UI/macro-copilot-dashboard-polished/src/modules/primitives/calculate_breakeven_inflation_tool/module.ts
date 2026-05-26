// ============================================================================
// src/modules/primitives/calculate_breakeven_inflation_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_breakeven_inflation_tool',
  tiers: ['generic_runnable'],
  displayName: 'Breakeven Inflation',
  category: 'cross_market_rv',
  oneLineSummary: 'Bond-implied breakeven inflation surfaced via the sovereign sub-agent — nominal sovereign yield minus the matched-tenor linker real yield in bps, with rolling 252-day z-score and a full chartable time series. Sibling of calculate_breakeven_inflation_simple_tool which is registered under the inflation_indexed_bonds sub-agent; the sovereign-side registration is preserved for backwards-compatible workflows that already named this tool under the sovereign catalogue.',
};
