// ============================================================================
// src/modules/primitives/calculate_cpi_surprise_tool/module.ts
// ----------------------------------------------------------------------------
// Surface-contract retraction (2026-05-26): the Stage 5 ``monitor_surface``
// + ``ask_surface`` claims were retracted because the shipped widget +
// card did not deliver value over the defaults (the Monitor widget was
// a stub; the bespoke Ask card was a strict regression vs the generic
// AssistantResearchCard).  Per the surface contract's containment
// principle, a module's ``tiers`` array MUST match delivery — claiming
// a tier you don't fully ship is "half-built state for the system",
// which the principle forbids.
//
// CPI Surprise is now an event-class primitive that's correctly
// surfaced via:
//   * Build — the generic schema-driven builder (``GenericPrimitiveBuilder``)
//     + ``AutoRenderer`` for the per-release series.
//   * Ask — the generic ``AssistantResearchCard`` (7 zones including
//     chart, DAG, provenance, follow-ups).  Once the Supervisor prompt
//     learns to route CPI queries to the inflation_swaps domain
//     (separate orchestration session), the generic card will render
//     the result properly.
//   * Library — auto-derived from the backend manifest.
//   * Monitor — NOT eligible.  CPI is a monthly event read, not a
//     desk-glanceable state.  Per the contract's Monitor eligibility
//     rule: event releases are excluded.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_cpi_surprise_tool',
  tiers: ['generic_runnable'],
  displayName: 'CPI Surprise',
  category: 'economic_release_surprises',
  oneLineSummary:
    "Per-release CPI surprise series (actual − consensus_median, in percentage points of YoY CPI) for one country's headline CPI YoY print, plus a rolling z-score over a window of N releases.",
  workspaceLabel: 'CPI surprise — release timeline + rolling z',
};
