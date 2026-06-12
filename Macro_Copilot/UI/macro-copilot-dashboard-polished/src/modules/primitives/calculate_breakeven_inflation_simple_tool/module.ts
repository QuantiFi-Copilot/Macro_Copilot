// ============================================================================
// src/modules/primitives/calculate_breakeven_inflation_simple_tool/module.ts
// ----------------------------------------------------------------------------
// Stage-B Phase-1 tool — brought to full parity with get_real_yield_level_tool
// under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// Design reference: mockups/Compact.png + mockups/Extended.png.
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS, LINKER_DEFAULT_TENORS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { BreakevenInflationWidget } from './surfaces/monitor/BreakevenInflationWidget';
import { BREAKEVEN_PAIR_OPTIONS } from './surfaces/breakevenShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_breakeven_inflation_simple_tool',

  // FM3 — tier claims (parity with get_real_yield_level_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — breakevens are a canonical morning-briefing read
  //     (surface_contract.md §3.4 eligibility)
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Breakeven Inflation',
  category: 'cross_market_rv',
  oneLineSummary:
    "What inflation rate is the bond market pricing in for the US, UK, France, or Canada? Breakeven inflation — the gap between a nominal government bond yield and the matching inflation-linked bond's real yield — with today's move and how stretched it is vs the past year. (Inflation compensation, not a clean expected-inflation read.)",


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  ``pair`` is the linker curve_family that uniquely picks the same-
  // country nominal counterparty; the widget expands it to both legs.
  // Fetches the SAME /api/v1/rates/detail/breakeven endpoint.
  monitorWidgets: [
    {
      id: 'breakeven_inflation_simple',
      label: 'Breakeven Inflation',
      description:
        'Bond-implied breakeven inflation (nominal − linker real) for one same-country pair. Inflation compensation, not expected inflation.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'pair',
          label: 'Country pair',
          defaultValue: 'USD_TIPS',
          options: BREAKEVEN_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '10Y',
          options: LINKER_DEFAULT_TENORS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: BreakevenInflationWidget,
    },
  ],

  // FM5 — defaults mirror Stage 1A exposure decisions in
  // breakeven_inflation_simple/config.yaml (exposed: field_name +
  // z_score_window_days / z_score_min_periods / z_score_ddof).
  defaultParams: {
    nominal_curve_family: 'UST',
    linker_curve_family: 'USD_TIPS',
    tenor: '10Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
