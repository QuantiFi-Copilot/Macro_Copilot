// ============================================================================
// src/modules/primitives/calculate_breakeven_inflation_simple_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_breakeven_inflation_simple_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_breakeven_inflation_simple',
  category: 'cross_market_rv',
  oneLineSummary: 'Bond-implied breakeven inflation at a matched (country, tenor) — nominal sovereign yield minus the linker real yield in bps, with rolling 252-day z-score, period changes and a full chartable time series. The "simple" name denotes the unadjusted nominal − real pairing (no carry / seasonals adjustment, which would be the TD #32 / TD #33 carry-adjusted primitive).',
};
