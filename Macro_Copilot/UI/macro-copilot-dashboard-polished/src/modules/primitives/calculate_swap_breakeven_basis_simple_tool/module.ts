// ============================================================================
// src/modules/primitives/calculate_swap_breakeven_basis_simple_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_swap_breakeven_basis_simple_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_swap_breakeven_basis_simple',
  category: 'cross_market_rv',
  oneLineSummary: 'Swap-vs-bond inflation basis (e.g. USD ZCIS 10Y minus US TIPS bond-implied breakeven 10Y) — the cleanest read of the swap-bond inflation pricing gap. The "simple" name denotes the unadjusted pairing (no carry / seasonals adjustment).',
};
