// ============================================================================
// src/modules/primitives/calculate_swap_spread_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_swap_spread_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_swap_spread',
  category: 'cross_market_rv',
  oneLineSummary: 'Cross-domain spread between a sovereign yield and an OIS rate at the same tenor (e.g. UST 10Y − SOFR 10Y) under the convention (sovereign − ois) × 100 bps, with snapshot, daily / weekly / monthly change, rolling z-score, trailing range, and time series.',
};
