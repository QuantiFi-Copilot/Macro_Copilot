// ============================================================================
// src/modules/primitives/get_ois_rate_level_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'get_ois_rate_level_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_ois_rate_level',
  category: 'snapshots',
  oneLineSummary: 'Single-tenor OIS par-swap-rate snapshot — current rate, daily / weekly / monthly change, rolling 252-day z-score, trailing 252-day high / low / percentile, plus full chartable time series.  OIS analogue of get_yield_levels.',
};
