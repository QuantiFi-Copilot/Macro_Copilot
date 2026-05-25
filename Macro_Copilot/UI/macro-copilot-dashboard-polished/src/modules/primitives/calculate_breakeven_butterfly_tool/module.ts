// ============================================================================
// src/modules/primitives/calculate_breakeven_butterfly_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_breakeven_butterfly_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_breakeven_butterfly',
  category: 'curve_shape',
  oneLineSummary: 'Three-point bond-implied breakeven butterfly with fixed 50-50 weights (e.g. US 2s5s10s breakeven butterfly) and rolling 252-day z-score. Breakeven analogue of calculate_butterfly.',
};
