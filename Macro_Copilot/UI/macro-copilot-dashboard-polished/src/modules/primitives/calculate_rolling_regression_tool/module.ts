// ============================================================================
// src/modules/primitives/calculate_rolling_regression_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_rolling_regression_tool',
  tiers: ['generic_runnable'],
  displayName: 'rolling_regression',
  category: 'rolling_analytics',
  oneLineSummary: 'Rolling OLS regression of one sovereign yield on one or more regressor yields via numpy.linalg.lstsq, returning per-regressor betas, alpha, residual, in-window R², and a condition-number quality flag.',
};
