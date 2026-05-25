// ============================================================================
// src/modules/primitives/calculate_curve_spread_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_curve_spread_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_curve_spread',
  category: 'curve_shape',
  oneLineSummary: 'Basis-point spread between two tenors on the same sovereign yield curve, with a fixed 1-year rolling z-score and full chartable time series.',
};
