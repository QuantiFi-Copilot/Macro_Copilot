// ============================================================================
// src/modules/primitives/calculate_zscore_custom_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_zscore_custom_tool',
  tiers: ['generic_runnable'],
  displayName: 'zscore_custom',
  category: 'rolling_analytics',
  oneLineSummary: 'Rolling z-score of a single sovereign yield with a user-supplied window length (vs the fixed 252-day window in get_yield_levels). Returns current z-score, latest yield, actual window parameters, and full z-score time series.',
};
