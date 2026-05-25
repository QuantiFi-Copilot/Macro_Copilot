// ============================================================================
// src/modules/primitives/compute_financing_rate_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'compute_financing_rate_tool',
  tiers: ['generic_runnable'],
  displayName: 'compute_financing_rate',
  category: 'panel_assembly',
  oneLineSummary: 'Daily financing-rate Panel keyed by `<curve_family>` for use as the financing input to the backtest workflow\'s evaluate_trades operator.  Multi-method primitive: V1 ships the `overnight_index_proxy` method (uses the OIS overnight index as a proxy for true overnight repo financing).  `term_repo_curve` and `gc_special_blend` are declared in the closed-method enum and refuse with NotImplementedError per PR11 — they unblock when TD #29 (term GC repo) and TD #30 (CUSIP-level repo specials) land.',
};
