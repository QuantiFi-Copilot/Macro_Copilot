// ============================================================================
// src/modules/primitives/calculate_otr_ofr_spread_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_otr_ofr_spread_tool',
  tiers: ['generic_runnable'],
  displayName: 'OTR OFR Spread',
  category: 'curve_shape',
  oneLineSummary: 'Basis-point yield spread between the on-the-run (OTR) bond and the first-off-the-run (OFR) bond for one (country, tenor) sovereign cash-bond slot — the desk-standard rich-cheap / liquidity-premium signal — plus its 252-trading-day rolling z-score and full chartable time series.  Resolves OTR/OFR per trade-date from macro_data.otr_history (ADR 0003) and pulls per-bond YLD_YTM_MID from macro_data.market_data_daily; no Bloomberg-computed quantity is recomputed (P12).',
};
