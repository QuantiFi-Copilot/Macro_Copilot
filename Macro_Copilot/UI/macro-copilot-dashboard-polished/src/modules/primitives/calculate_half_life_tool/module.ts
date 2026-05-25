// ============================================================================
// src/modules/primitives/calculate_half_life_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_half_life_tool',
  tiers: ['generic_runnable'],
  displayName: 'half_life',
  category: 'model_fits',
  oneLineSummary: 'Ornstein-Uhlenbeck / AR(1) fit on a supplied series.  Returns half-life of mean reversion (trading days), long-run mean, current deviation, OU β with confidence interval, and a delta-method CI on the half-life itself.',
};
