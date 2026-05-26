// ============================================================================
// src/modules/primitives/calculate_ois_forward_rate_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_ois_forward_rate_tool',
  tiers: ['generic_runnable'],
  displayName: 'OIS Forward Rate',
  category: 'forwards_classify',
  oneLineSummary: 'Implied forward rate between two points on an OIS curve via a dual-compounding bootstrap (simple interest for T ≤ 1Y, annual compounding for T > 1Y).  Two input modes — tenor-based or date-based — both returning forward rate, daily change, rolling z-score, trailing range, and time series.',
};
