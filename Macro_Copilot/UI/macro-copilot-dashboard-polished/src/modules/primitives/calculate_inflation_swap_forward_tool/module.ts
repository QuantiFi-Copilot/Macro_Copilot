// ============================================================================
// src/modules/primitives/calculate_inflation_swap_forward_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_inflation_swap_forward_tool',
  tiers: ['generic_runnable'],
  displayName: 'Inflation Swap Forward',
  category: 'forwards_classify',
  oneLineSummary: 'Forward inflation swap rate (e.g. 5Y5Y inflation) computed from two spot ZCIS legs via closed-form forward construction. ZCIS analogue of calculate_ois_forward_rate.',
};
