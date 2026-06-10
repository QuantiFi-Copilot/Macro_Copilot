// ============================================================================
// src/modules/primitives/calculate_cross_country_real_yield_spread_simple_tool/module.ts
// ----------------------------------------------------------------------------
// Second inflation_indexed_bonds CROSS-COUNTRY tool brought to parity with
// calculate_cross_country_breakeven_spread_simple_tool under the dual-view
// + standalone-bridge contracts:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/cross-country-real-yield-spread +
//     own surfaces; no shared typedView)
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
import { CrossCountryRealYieldSpreadWidget } from './surfaces/monitor/CrossCountryRealYieldSpreadWidget';
import {
  CURVE_FAMILY_OPTIONS,
  TENOR_OPTIONS,
} from './surfaces/crossCountryRealYieldSpreadSimpleShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_cross_country_real_yield_spread_simple_tool',

  // FM3 — tier claims (parity with calculate_cross_country_breakeven_spread_simple_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — cross-country linker real-yield spreads are a
  //     desk-canonical cross-CB real-rate-divergence read; surfacing them
  //     as glanceable tiles fits surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Cross-Country Linker Real-Yield Spread',
  category: 'cross_market_rv',
  oneLineSummary:
    'Same-tenor cross-country linker real-yield spread (e.g. USD_TIPS 10Y real yield minus GBP_LINKER 10Y real yield) — daily/weekly/monthly bps changes of a percent-units spread, 252-day rolling z-score, trailing range, per-curve real-yield level decomposition. Surfaces the load-bearing index-family mismatch caveat (CPI-U / RPI / HICPxT / Canada CPI are NOT fungible inflation measures) AND the cross-country linker market-structure caveat (liquidity / issuance / on-the-run differences) so the spread is read as a mix of real-rate divergence and structural differences, not a clean real-rate read.',

  // FM9 — STANDALONE pattern (methodology_exposure.md §5): no shared typedView.
  typedView: null,
  richModel: false,

  // FM8 — dual Build-side surfaces (rendering_density.md §5).  ``build`` is
  // kept === buildExtended for the legacy VirtualPrimitiveCanvas dispatcher.
  surfaces: {
    build: BuildExtended,
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  Two-curve, single-tenor primitive — exposes both linker curve
  // selects + tenor + lookback.  Fetches the SAME
  // /api/v1/rates/detail/cross-country-real-yield-spread endpoint the
  // Build views use (standalone bridge §5.4).
  monitorWidgets: [
    {
      id: 'cross_country_real_yield_spread',
      label: 'Cross-Country Real-Yield Spread',
      description:
        'Same-tenor cross-country linker real-yield spread between two sovereign linker curves (e.g. USD_TIPS 10Y RY - GBP_LINKER 10Y RY).  Cross-country REAL-RATE differential; spread is in PERCENT (real yields are quoted in PERCENT — NOT BPS).  Different countries\' linkers reference DIFFERENT inflation indices (CPI-U / RPI / HICPxT / Canada CPI — NOT fungible) AND cross-country linker markets carry materially different liquidity / market-structure characteristics.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'first_curve_family',
          label: 'First curve',
          defaultValue: 'GBP_LINKER',
          options: CURVE_FAMILY_OPTIONS,
        },
        {
          kind: 'select',
          name: 'second_curve_family',
          label: 'Second curve',
          defaultValue: 'USD_TIPS',
          options: CURVE_FAMILY_OPTIONS,
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
      component: CrossCountryRealYieldSpreadWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic
  // schema requires.  ``field_name`` defaults to empty string (the wire
  // sentinel coerced to None) so the YAML's default_field_name
  // (YLD_YTM_MID) flows through.  Mockup defaults: GBP_LINKER vs
  // USD_TIPS at 10Y (UK 10Y RY minus US 10Y RY — the mockup's headline
  // pair).
  defaultParams: {
    first_curve_family: 'GBP_LINKER',
    second_curve_family: 'USD_TIPS',
    tenor: '10Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
