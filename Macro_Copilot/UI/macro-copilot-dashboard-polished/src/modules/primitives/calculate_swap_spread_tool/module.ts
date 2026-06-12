// ============================================================================
// src/modules/primitives/calculate_swap_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Factory dispatch — brought to full parity with the
// calculate_breakeven_inflation_simple_tool reference (the single-spread BPS
// dual-view pilot shape) and the OIS-family siblings
// calculate_ois_cross_market_spread_tool / calculate_ois_curve_spread_tool
// (same risk-neutral OIS leg, same TS-side methodology disclosure marker
// pending PR10) under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// Design reference: mockups/Compact.png + mockups/Extended.png.
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { SwapSpreadWidget } from './surfaces/monitor/SwapSpreadWidget';
import {
  SWAP_SPREAD_PAIR_OPTIONS,
  SWAP_SPREAD_TENOR_OPTIONS_BY_PAIR,
} from './surfaces/swapSpreadShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_swap_spread_tool',

  // FM3 — tier claims (parity with calculate_breakeven_inflation_simple_tool +
  // calculate_ois_cross_market_spread_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — swap spreads are the desk-canonical RV read between
  //     treasuries and the OIS curve; surfacing them as glanceable tiles fits
  //     surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Swap Spread',
  category: 'cross_market_rv',
  oneLineSummary:
    'Cross-domain swap spread between a sovereign yield and the matching-currency OIS rate at the same tenor (e.g. UST 10Y minus USD_SOFR_OIS 10Y) — sign convention POSITIVE = treasuries trade CHEAP to OIS. Par-leg OIS approximation (NOT per-bond ASW). Daily/weekly/monthly bps changes, 252-day rolling z-score, trailing range. Canonical desk RV read between cash and swap markets.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  ``pair`` is the sovereign curve_family that uniquely picks the
  // canonical same-currency OIS counterparty (a swap spread is a same-
  // currency object; the widget expands it to both legs).  Fetches the SAME
  // /api/v1/rates/detail/swap-spread endpoint the Build views use (standalone
  // bridge §5.4).
  monitorWidgets: [
    {
      id: 'swap_spread',
      label: 'Swap Spread',
      description:
        'Same-currency swap spread between a sovereign yield curve and the canonical OIS curve at one tenor (e.g. UST 10Y minus USD_SOFR_OIS 10Y). Sign convention POSITIVE = treasuries cheap to OIS. Par-leg OIS approximation, NOT per-bond ASW.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'pair',
          label: 'Sovereign Leg',
          defaultValue: 'UST',
          options: SWAP_SPREAD_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '10Y',
          options: SWAP_SPREAD_TENOR_OPTIONS_BY_PAIR.UST,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: SwapSpreadWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic schema
  // requires.  Mockup default: UST vs USD_SOFR_OIS 10Y (the desk-canonical
  // USD 10Y swap spread).  ``sovereign_field_name`` defaults to 'YLD_YTM_MID'
  // (sovereign bond yield-to-maturity); ``ois_field_name`` defaults to
  // 'PX_LAST' (OIS par swap rate) — distinct Bloomberg field-name mnemonics
  // per leg per the YAML conventions.  No z-score / min-periods / ddof
  // exposed on this primitive — those are YAML-locked (mirrors the OIS
  // curve_spread / cross-market / butterfly bridges).
  defaultParams: {
    sovereign_curve_family: 'UST',
    ois_curve_family: 'USD_SOFR_OIS',
    tenor: '10Y',
    lookback_days: '365',
    sovereign_field_name: 'YLD_YTM_MID',
    ois_field_name: 'PX_LAST',
  },
};
