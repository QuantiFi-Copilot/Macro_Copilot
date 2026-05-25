// ============================================================================
// src/modules/primitives/calculate_yield_change_attribution_pca_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_yield_change_attribution_pca_tool',
  tiers: ['generic_runnable'],
  displayName: 'yield_change_attribution_pca',
  category: 'model_fits',
  oneLineSummary: 'Decompose a sovereign yield change at a given tenor over a window into per-PCA-component contributions in bps.  Loadings come from an inline PCA fit or a caller-supplied pasted payload.',
};
