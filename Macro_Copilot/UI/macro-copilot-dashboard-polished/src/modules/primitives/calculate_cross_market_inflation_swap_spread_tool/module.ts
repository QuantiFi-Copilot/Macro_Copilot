// ============================================================================
// src/modules/primitives/calculate_cross_market_inflation_swap_spread_tool/module.ts
// ----------------------------------------------------------------------------
// First inflation_swaps tool brought to parity with the
// calculate_breakeven_inflation_simple_tool / calculate_breakeven_butterfly_tool
// references under the dual-view + standalone-bridge contracts:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/cross-market-zcis + own surfaces; no shared
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
import { CrossMarketZcisWidget } from './surfaces/monitor/CrossMarketZcisWidget';
import {
  ZCIS_FAMILY_OPTIONS,
  ZCIS_TENOR_OPTIONS,
} from './surfaces/crossMarketZcisShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_cross_market_inflation_swap_spread_tool',

  // FM3 — tier claims (parity with calculate_breakeven_inflation_simple_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — cross-market ZCIS spreads are a desk-canonical
  //     cross-CB inflation-divergence read; surfacing them as glanceable
  //     tiles fits surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Cross-Market Inflation Swap Spread',
  category: 'cross_market_rv',
  oneLineSummary:
    'Same-tenor cross-market ZCIS spread (e.g. USD_ZCIS 5Y minus EUR_ZCIS 5Y) — daily/weekly/monthly bps changes, 252-day rolling z-score, trailing range. Surfaces the load-bearing index-family caveat (CPI-U vs HICPxT vs RPI are NOT fungible inflation measures) so the spread is read as inflation-compensation divergence, not pure expected-inflation divergence.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  Two-curve, single-tenor primitive — exposes both legs + tenor +
  // lookback.  Fetches the SAME /api/v1/rates/detail/cross-market-zcis
  // endpoint the Build views use (standalone bridge §5.4).
  monitorWidgets: [
    {
      id: 'cross_market_zcis_spread',
      label: 'Cross-Market ZCIS Spread',
      description:
        'Same-tenor cross-market ZCIS spread between two ZCIS curve families (e.g. USD_ZCIS - EUR_ZCIS 5Y). Inflation-compensation divergence across distinct index families (CPI-U / HICPxT / RPI not fungible).',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'leg_a_curve_family',
          label: 'Leg A',
          defaultValue: 'USD_ZCIS',
          options: ZCIS_FAMILY_OPTIONS,
        },
        {
          kind: 'select',
          name: 'leg_b_curve_family',
          label: 'Leg B',
          defaultValue: 'EUR_ZCIS',
          options: ZCIS_FAMILY_OPTIONS,
        },
        {
          kind: 'select',
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '5Y',
          options: ZCIS_TENOR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: CrossMarketZcisWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic
  // schema requires.  ``field_name`` defaults to empty string (the wire
  // sentinel coerced to None by the schema's _coerce_empty_field_name
  // validator) so the YAML's default_zcis_rate_field (PX_MID) flows
  // through.  Mockup defaults: USD_ZCIS vs EUR_ZCIS 5Y.
  defaultParams: {
    leg_a_curve_family: 'USD_ZCIS',
    leg_b_curve_family: 'EUR_ZCIS',
    tenor: '5Y',
    lookback_days: '365',
    field_name: 'PX_MID',
  },
};
