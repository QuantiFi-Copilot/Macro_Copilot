// ============================================================================
// src/modules/primitives/calculate_otr_ofr_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Surface-contract retraction (2026-05-26): the Stage 6 ``monitor_surface``
// claim was retracted because the shipped widget was a stub (no real
// fetch, no chart, only placeholder text).  Per the surface contract's
// containment principle, a module's ``tiers`` array MUST match delivery
// — claiming a tier you don't fully ship is "half-built state for the
// system", which the principle forbids.
//
// OTR-OFR Spread IS Monitor-eligible per the contract — it's the
// desk-standard cash-bond rich-cheap / liquidity-premium signal that
// the desk parks on the board across (curve, tenor) variants and reads
// through the day.  But shipping a real parameterised widget requires
// the typed-detail endpoint to expose OTR-OFR series data first.  Once
// the backend ships that endpoint, a real ``monitor_surface`` claim
// + non-stub widget can land in a single PR.
//
// Currently surfaced via:
//   * Build — generic schema-driven builder + AutoRenderer for the
//     per-trade-date spread series.
//   * Ask — generic AssistantResearchCard (once Supervisor routing for
//     OTR-OFR queries lands in a separate orchestration session).
//   * Library — auto-derived from the backend manifest.
//   * Monitor — ELIGIBLE but DEFERRED.  Tracked in the surface contract
//     registry; will be claimed when the typed-detail endpoint ships +
//     a real widget is built inside this module folder.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_otr_ofr_spread_tool',
  tiers: ['generic_runnable'],
  displayName: 'OTR-OFR Spread',
  category: 'curve_shape',
  oneLineSummary:
    'Basis-point yield spread between the on-the-run (OTR) bond and the first-off-the-run (OFR) bond for one (country, tenor) sovereign cash-bond slot — the desk-standard rich-cheap / liquidity-premium signal — plus its 252-trading-day rolling z-score and full chartable time series.',
  workspaceLabel: 'OTR-OFR spread chart + history',
};
