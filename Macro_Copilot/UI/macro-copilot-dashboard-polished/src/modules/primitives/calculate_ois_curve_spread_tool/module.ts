// ============================================================================
// src/modules/primitives/calculate_ois_curve_spread_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_ois_curve_spread_tool',
  tiers: ['generic_runnable'],
  displayName: 'OIS Curve Spread',
  category: 'curve_shape',
  oneLineSummary: 'Basis-point spread between two tenors on the same OIS curve (e.g. SOFR 2s10s) with rolling 252-day z-score, daily change, and both canonical and bespoke wire-frozen time series.',
};
