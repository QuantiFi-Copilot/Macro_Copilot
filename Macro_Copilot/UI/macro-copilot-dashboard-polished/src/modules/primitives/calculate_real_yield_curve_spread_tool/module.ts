// ============================================================================
// src/modules/primitives/calculate_real_yield_curve_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Stage-C Phase-1 tool — brought to full parity with get_real_yield_level_tool
// under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/real_yield_curve_spread + own surfaces)
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact)
//
// Design reference: mockups/Compact.png + mockups/Extended.png.
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import {
  LINKER_CURVE_OPTIONS,
  LINKER_DEFAULT_TENORS,
  LOOKBACK_OPTIONS,
} from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { RealYieldCurveSpreadWidget } from './surfaces/monitor/RealYieldCurveSpreadWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity
  toolName: 'calculate_real_yield_curve_spread_tool',

  // FM3 — tier claims (parity with get_real_yield_level_tool):
  //   * generic_runnable
  //   * custom_build_surface — dual-view Build
  //   * monitor_surface — real-yield 5s30s / 2s10s curve shape is a desk
  //     daily-glance read (surface_contract.md §3.4)
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Real-Yield Curve Spread',
  category: 'curve_shape',
  oneLineSummary:
    "How steep is a country's real-yield curve right now? The real-yield curve spread between two tenors of US TIPS, UK linkers, French OATei, or Canadian RRBs (e.g. TIPS 5s10s) — the curve shape, today's move, and how stretched it is vs the past year.",


  // FM8 — dual Build-side surfaces.  ``build`` === buildExtended for the
  // legacy dispatcher.
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  short_tenor / long_tenor offered as flat tenor selects; the
  // backend re-validates the long > short rule and returns a controlled
  // error envelope (rendered in the widget) for an invalid pair.
  monitorWidgets: [
    {
      id: 'real_yield_curve_spread',
      label: 'Real-Yield Curve Spread',
      description:
        'Real-yield curve spread (long − short real yield) for one sovereign-linker curve. Curve shape of the real-rate term structure.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family',
          label: 'Linker curve',
          defaultValue: 'USD_TIPS',
          options: LINKER_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'short_tenor',
          label: 'Short tenor',
          defaultValue: '5Y',
          options: LINKER_DEFAULT_TENORS,
        },
        {
          kind: 'select',
          name: 'long_tenor',
          label: 'Long tenor',
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
      component: RealYieldCurveSpreadWidget,
    },
  ],

  // FM5 — defaults mirror Stage 1A exposure decisions in
  // real_yield_curve_spread/config.yaml.
  defaultParams: {
    curve_family: 'USD_TIPS',
    short_tenor: '5Y',
    long_tenor: '10Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
