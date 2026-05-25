// ============================================================================
// src/modules/primitives/calculate_beta_adjusted_spread_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_beta_adjusted_spread_tool',
  tiers: ['generic_runnable'],
  displayName: 'beta_adjusted_spread',
  category: 'rolling_analytics',
  oneLineSummary: 'Bivariate beta-adjusted RV — rolling OLS regresses one sovereign yield (target) on another (regressor); returns hedge ratio (beta), alpha (yield-percent), residual in bps, and a rolling z-score on the residual.',
};
