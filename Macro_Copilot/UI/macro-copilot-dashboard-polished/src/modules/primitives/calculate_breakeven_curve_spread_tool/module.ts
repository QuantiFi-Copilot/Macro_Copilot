// ============================================================================
// src/modules/primitives/calculate_breakeven_curve_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Round-1 dispatch — brought to full parity with the
// calculate_breakeven_butterfly_tool / calculate_real_yield_curve_spread_tool
// references under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/breakeven-curve-spread + own surfaces; no
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
import { BreakevenCurveSpreadWidget } from './surfaces/monitor/BreakevenCurveSpreadWidget';
import {
  BREAKEVEN_CURVE_SPREAD_PAIR_OPTIONS,
  BREAKEVEN_CURVE_SPREAD_TENORS_BY_PAIR,
} from './surfaces/breakevenCurveSpreadShared';

// Union of valid tenor labels across all four linker pairs.  The static
// catalog form layer does not (yet) support cross-field dynamic options,
// so we surface the UNION of tenors; tenors that are not valid for the
// chosen pair fall back gracefully in the widget (it snaps to the pair's
// first tenor pair).  This mirrors the breakeven_butterfly module's
// triplet-options strategy.
const TENOR_UNION_OPTIONS = (() => {
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const tenors of Object.values(BREAKEVEN_CURVE_SPREAD_TENORS_BY_PAIR)) {
    for (const t of tenors) {
      if (seen.has(t.value)) continue;
      seen.add(t.value);
      out.push({ value: t.value, label: t.label });
    }
  }
  return out;
})();

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_breakeven_curve_spread_tool',

  // FM3 — tier claims (parity with calculate_breakeven_butterfly_tool /
  // calculate_real_yield_curve_spread_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — same-country breakeven curve spreads (2s10s /
  //     5s30s) are a desk daily-glance read for inflation-compensation
  //     term-structure shape (surface_contract.md §3.4).
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Breakeven Curve Spread',
  category: 'cross_market_rv',
  oneLineSummary:
    'Same-country breakeven curve spread between two tenors of one bond-implied breakeven curve (e.g. US 2s10s breakeven, UK 5s30s breakeven). Positive = upward-sloping inflation compensation curve; negative = inverted. Inflation compensation term structure, not pure expected-inflation term structure.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  ``pair`` is the linker curve_family that uniquely picks the same-
  // country nominal counterparty; ``short_tenor`` / ``long_tenor`` are the
  // two endpoint tenors (long > short enforced by the Pydantic
  // strict-ordering validator).  Fetches the SAME
  // /api/v1/rates/detail/breakeven-curve-spread endpoint.
  monitorWidgets: [
    {
      id: 'breakeven_curve_spread',
      label: 'Breakeven Curve Spread',
      description:
        'Same-country breakeven curve spread (long − short) between two tenors of one bond-implied breakeven curve. Inflation compensation term structure, not pure expected inflation.',
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
          options: BREAKEVEN_CURVE_SPREAD_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'short_tenor',
          label: 'Short tenor',
          defaultValue: '2Y',
          options: TENOR_UNION_OPTIONS,
        },
        {
          kind: 'select',
          name: 'long_tenor',
          label: 'Long tenor',
          defaultValue: '10Y',
          options: TENOR_UNION_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: BreakevenCurveSpreadWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic
  // schema requires (no exposed z-score conventions on this primitive —
  // the curve-spread schema keeps them YAML-only).
  defaultParams: {
    nominal_curve_family: 'UST',
    linker_curve_family: 'USD_TIPS',
    short_tenor: '2Y',
    long_tenor: '10Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
