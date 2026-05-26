// ============================================================================
// src/modules/primitives/get_otr_history_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'get_otr_history_tool',
  tiers: ['workflow_incompatible'],
  displayName: 'OTR History',
  category: 'snapshots',
  oneLineSummary: 'On-the-run transition log for one (country, tenor) sovereign cash-bond slot — current OTR snapshot (CUSIP, ISIN, vendor_ticker, maturity_date, effective_from of the open window) plus the chronological list of OTR transitions intersecting the lookback window.  Pure-INGEST read of macro_data.otr_history (ADR 0003), forward-only per ADR 0007 / TD #27.',
  unsupportedReason: {
    label: 'get_otr_history',
    reason: 'Backend declares this tool in `WORKFLOW_INCOMPATIBLE_TOOLS`.  Backend rationale: SCD2 transition log + identifier snapshot (CUSIP / ISIN / vendor_ticker / maturity_date + effective_from / effective_to date ranges). List-shaped categorical output, not a numeric ``TimeSeries`` or wide-format ``Panel``; bridge cannot dispatch it. Se',
    whatWorksNow: 'Ask can run the tool via MCP; a bespoke Build typed-view may exist via the rates typed-detail endpoints.',
  },
};
