// ============================================================================
// src/modules/primitives/calculate_real_yield_butterfly_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_real_yield_butterfly_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_real_yield_butterfly',
  category: 'curve_shape',
  oneLineSummary: 'Three-point real-yield butterfly with fixed 50-50 weights (e.g. US TIPS 2s5s10s butterfly) and rolling 252-day z-score. Real-yield analogue of calculate_butterfly.',
};
