// ============================================================================
// src/modules/primitives/calculate_real_yield_curve_spread_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_real_yield_curve_spread_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_real_yield_curve_spread',
  category: 'curve_shape',
  oneLineSummary: 'Basis-point spread between two tenors of one country\'s linker real-yield curve (e.g. US TIPS 5s30s) with rolling 252-day z-score, daily change, and a full chartable time series. Real-yield analogue of calculate_curve_spread.',
};
