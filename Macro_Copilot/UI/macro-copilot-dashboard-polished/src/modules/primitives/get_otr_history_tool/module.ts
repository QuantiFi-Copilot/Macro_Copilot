// ============================================================================
// src/modules/primitives/get_otr_history_tool/module.ts — Dual-view build.
// ----------------------------------------------------------------------------
// Stage upgrade: Stage 3 scaffold (runtime tier only) → standalone-bridge
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/otr-history + own surfaces; no
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// RUNTIME TIER — ``workflow_incompatible`` is KEPT (backend declares the
// tool in WORKFLOW_INCOMPATIBLE_TOOLS: SCD2 transition log + identifier
// snapshot, no Series / Panel artifact for the workflow bridge).  The
// runtime tier and the capability tiers are ORTHOGONAL axes:
// workflow-incompatible only says the WORKFLOW BRIDGE can't dispatch it —
// the typed-detail endpoint is fully live, so the module also claims
// ``custom_build_surface`` on top of the unchanged runtime tier.
// Precedent: calculate_wirp_meeting_pricing_tool ships the same combo.
// ``unsupportedReason`` stays VERBATIM (FM6 — the runtime-status copy
// mirrors the backend rationale; ``whatWorksNow`` already discloses the
// bespoke surfaces).
//
// CATEGORICAL SCD2 TIMELINE shape (rendering_density.md §2.2 semantic
// contract): the wire is an identity snapshot + dated identifier windows
// with NO numeric series, so the compact view is a KPI row + recent-
// windows TABLE (no sparkline — a chart would fabricate a series) and the
// extended view is a benchmark-succession canvas (KPI strip + full SCD2
// timeline + verbatim methodology_note).
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No side
// effects, no global mutation, no register() calls.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'get_otr_history_tool',

  // FM3 — tier claims:
  //   * workflow_incompatible — runtime-status tier KEPT verbatim from the
  //     Stage-3 scaffold (backend WORKFLOW_INCOMPATIBLE_TOOLS entry —
  //     categorical SCD2 output, not bridge-composable in V1).
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //     served by the standalone typed-detail bridge, which is live even
  //     though the workflow bridge can't dispatch the tool.
  tiers: ['workflow_incompatible', 'custom_build_surface'],

  // FM5 — display metadata (kept verbatim from the Stage-3 scaffold)
  displayName: 'OTR History',
  category: 'snapshots',
  oneLineSummary: 'On-the-run transition log for one (country, tenor) sovereign cash-bond slot — current OTR snapshot (CUSIP, ISIN, vendor_ticker, maturity_date, effective_from of the open window) plus the chronological list of OTR transitions intersecting the lookback window.  Pure-INGEST read of macro_data.otr_history (ADR 0003), forward-only per ADR 0007 / TD #27.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5 — defaults mirror the Pydantic Input's structural fields plus
  // the YAML lookback default (365).
  defaultParams: {
    country: 'US',
    tenor: '10Y',
    lookback_days: '365',
  },

  // FM6 — unsupported reason: KEPT VERBATIM from the Stage-3 scaffold
  // (mirrors the backend WORKFLOW_INCOMPATIBLE_TOOLS rationale).  Note
  // ``whatWorksNow`` already anticipated the bespoke Build typed-view
  // this module now ships.
  unsupportedReason: {
    label: 'get_otr_history',
    reason: 'Backend declares this tool in `WORKFLOW_INCOMPATIBLE_TOOLS`.  Backend rationale: SCD2 transition log + identifier snapshot (CUSIP / ISIN / vendor_ticker / maturity_date + effective_from / effective_to date ranges). List-shaped categorical output, not a numeric ``TimeSeries`` or wide-format ``Panel``; bridge cannot dispatch it. Se',
    whatWorksNow: 'Ask can run the tool via MCP; a bespoke Build typed-view may exist via the rates typed-detail endpoints.',
  },
};
