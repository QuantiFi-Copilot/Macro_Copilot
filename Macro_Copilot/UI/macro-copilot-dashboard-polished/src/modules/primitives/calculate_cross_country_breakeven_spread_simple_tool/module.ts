// ============================================================================
// src/modules/primitives/calculate_cross_country_breakeven_spread_simple_tool/module.ts
// ----------------------------------------------------------------------------
// First inflation_indexed_bonds CROSS-COUNTRY tool brought to parity with
// calculate_cross_market_inflation_swap_spread_tool under the dual-view +
// standalone-bridge contracts:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/cross-country-breakeven-spread + own
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// Design reference: mockups/Compact.png + mockups/Extended.png.
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { CrossCountryBreakevenSpreadWidget } from './surfaces/monitor/CrossCountryBreakevenSpreadWidget';
import {
  COUNTRY_PAIR_OPTIONS,
  TENOR_OPTIONS,
} from './surfaces/crossCountryBreakevenSpreadSimpleShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_cross_country_breakeven_spread_simple_tool',

  // FM3 — tier claims (parity with calculate_cross_market_inflation_swap_spread_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — cross-country breakeven spreads are a desk-canonical
  //     cross-CB inflation-divergence read; surfacing them as glanceable
  //     tiles fits surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Cross-Country Bond Breakeven Spread',
  category: 'cross_market_rv',
  oneLineSummary:
    'Same-tenor cross-country bond-implied breakeven spread (e.g. UK 10Y BE minus US 10Y BE) — daily/weekly/monthly bps changes, 252-day rolling z-score, trailing range, per-leg breakeven decomposition. Surfaces the load-bearing index-family-mismatch caveat (CPI-U / RPI / HICPxT / Canada CPI are NOT fungible inflation measures) so the spread is read as cross-country inflation-compensation divergence, not pure expected-inflation divergence.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  Two-country, single-tenor primitive — exposes both country pair
  // selects + tenor + lookback.  Fetches the SAME
  // /api/v1/rates/detail/cross-country-breakeven-spread endpoint the Build
  // views use (standalone bridge §5.4).
  monitorWidgets: [
    {
      id: 'cross_country_breakeven_spread',
      label: 'Cross-Country Breakeven Spread',
      description:
        'Same-tenor cross-country bond-implied breakeven spread between two sovereign (nominal, linker) pairs (e.g. UK 10Y BE - US 10Y BE). Cross-country inflation-compensation divergence; the underlying linkers reference different inflation indices (CPI-U / RPI / HICPxT / Canada CPI — NOT fungible).',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'country_a_pair',
          label: 'Country A',
          defaultValue: 'UK_GILT/GBP_LINKER',
          options: COUNTRY_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'country_b_pair',
          label: 'Country B',
          defaultValue: 'UST/USD_TIPS',
          options: COUNTRY_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '10Y',
          options: TENOR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: CrossCountryBreakevenSpreadWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic
  // schema requires.  ``field_name`` defaults to empty string (the wire
  // sentinel coerced to None) so the YAML's default_field_name
  // (YLD_YTM_MID) flows through.  Mockup defaults: UK_GILT/GBP_LINKER vs
  // UST/USD_TIPS at 10Y.
  defaultParams: {
    country_a_pair: 'UK_GILT/GBP_LINKER',
    country_b_pair: 'UST/USD_TIPS',
    tenor: '10Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
