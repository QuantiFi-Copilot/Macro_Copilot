// ============================================================================
// src/modules/primitives/calculate_cross_country_real_yield_spread_simple_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_cross_country_real_yield_spread_simple_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_cross_country_real_yield_spread_simple',
  category: 'cross_market_rv',
  oneLineSummary: 'Cross-country sovereign-linker real-yield spread (e.g. US TIPS vs UK linker 10Y) — country-A real yield minus country-B real yield at a matched tenor, with rolling 252-day z-score. The "simple" name denotes the unadjusted cross-country pairing (no FX adjustment).',
};
