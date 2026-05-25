// ============================================================================
// src/modules/primitives/calculate_cross_market_inflation_swap_spread_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_cross_market_inflation_swap_spread_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_cross_market_inflation_swap_spread',
  category: 'cross_market_rv',
  oneLineSummary: 'Cross-country ZCIS spread (e.g. USD ZCIS vs EUR ZCIS 10Y) — one market\'s ZCIS rate minus another\'s at a matched tenor, with rolling 252-day z-score and a full chartable time series.',
};
