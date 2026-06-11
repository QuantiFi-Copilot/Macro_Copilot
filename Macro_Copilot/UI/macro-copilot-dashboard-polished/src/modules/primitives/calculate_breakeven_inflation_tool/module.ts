// ============================================================================
// src/modules/primitives/calculate_breakeven_inflation_tool/module.ts
// ----------------------------------------------------------------------------
// RETIRED FROM BESPOKE-SURFACE SCOPE (consolidation, 2026-06-11).
//
// This is the ONE true cross-domain duplicate in the catalogue: the
// sovereign-side sibling of ``calculate_breakeven_inflation_simple_tool``
// (inflation_indexed_bonds domain), which ships the canonical polished
// dual-view (the Phase-1 pilot).  The sovereign manifest itself says this
// registration is "preserved for backwards-compatible workflows that
// already named this tool under the sovereign catalogue".
//
// Decision (G-3.1d): DO NOT build a bespoke surface here — building one
// would duplicate the sibling's UI for the same desk concept (P10).  A
// surface *redirect* was evaluated and REJECTED: the two Inputs are not
// param-compatible (this tool: ``real_curve_family`` + ``convention`` +
// ``nominal_field_name``/``real_field_name``; the sibling:
// ``linker_curve_family`` + ``field_name`` + z-score knobs) — silently
// re-mapping or dropping params in a wrapper would violate P5/P6 honesty.
//
// End state: the tool stays ``generic_runnable`` (it IS runnable — the
// backend keeps it in ``_PRIMITIVE_SPECS`` for backwards-compatible
// workflows, so FM12/parity require the runnable tier), renders through
// the generic schema-driven builder + artifact-type widgets in DAG /
// workspace contexts, and the Library copy points users at the sibling's
// canonical surface.  This module is the DOCUMENTED EXCEPTION to the
// "every primitive ships dual-view" rule — see
// tmp/prompt_tests/FRONTEND_CONSOLIDATION_AUDIT.md (dedup note) and
// THESIS.md Q3/Q4.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_breakeven_inflation_tool',
  tiers: ['generic_runnable'],
  displayName: 'Breakeven Inflation (Sovereign Catalogue)',
  category: 'cross_market_rv',
  oneLineSummary:
    'Bond-implied breakeven inflation surfaced via the sovereign sub-agent — nominal sovereign yield minus the matched-tenor linker real yield in bps, with rolling 252-day z-score and a full chartable time series. Sibling of calculate_breakeven_inflation_simple_tool (inflation domain), which carries the canonical Breakeven Inflation surface; this sovereign-side registration is preserved for backwards-compatible workflows.',
  workspaceLabel:
    'Breakeven (sovereign catalogue) — see Breakeven Inflation for the full surface',
};
