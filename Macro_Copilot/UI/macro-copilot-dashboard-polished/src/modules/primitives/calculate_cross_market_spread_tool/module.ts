// ============================================================================
// src/modules/primitives/calculate_cross_market_spread_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_cross_market_spread_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_cross_market_spread',
  category: 'cross_market_rv',
  oneLineSummary: 'Yield differential between the same tenor on two sovereign curves (e.g. BTP-Bund 10Y), in basis points, with rolling 252-day z-score, daily / weekly / monthly change, trailing range, and full time series.',
};
