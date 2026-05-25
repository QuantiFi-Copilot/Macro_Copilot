// ============================================================================
// src/modules/primitives/calculate_pca_yield_curve_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_pca_yield_curve_tool',
  tiers: ['generic_runnable'],
  displayName: 'pca_yield_curve',
  category: 'model_fits',
  oneLineSummary: 'PCA on the yield-CHANGES panel of one sovereign curve.  Returns per-component loadings, variance shares, factor-score time series, and per-component quality metadata (degenerate + sign-anchor flags).',
};
