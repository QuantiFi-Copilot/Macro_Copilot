// ============================================================================
// src/modules/primitives/calculate_forward_breakeven_simple_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_forward_breakeven_simple_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_forward_breakeven_simple',
  category: 'forwards_classify',
  oneLineSummary: 'Forward bond-implied breakeven (e.g. 5Y5Y breakeven) computed from two spot bond-implied breakeven legs. The "simple" name denotes the textbook log-additive forward construction without carry / seasonals adjustments.',
};
