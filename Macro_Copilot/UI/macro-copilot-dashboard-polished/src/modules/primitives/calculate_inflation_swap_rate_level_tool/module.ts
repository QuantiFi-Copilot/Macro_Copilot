// ============================================================================
// src/modules/primitives/calculate_inflation_swap_rate_level_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_inflation_swap_rate_level_tool',
  tiers: ['generic_runnable'],
  displayName: 'Inflation Swap Rate Level',
  category: 'snapshots',
  oneLineSummary: 'Single-pillar zero-coupon inflation swap (ZCIS) rate snapshot — current rate, daily / weekly / monthly change in bps, rolling 252-day z-score, trailing 252-day high / low / percentile, and a full chartable time series. ZCIS analogue of get_ois_rate_level.',
};
