// ============================================================================
// src/modules/primitives/calculate_inflation_swap_curve_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view + monitor module under the standards:
//   - methodology_exposure.md §5 standalone-bridge contract (this tool ships
//     its own typed-detail endpoint at
//     /api/v1/rates/detail/inflation-swap-curve-spread + its own frontend
//     surfaces; no shared typedView reuse)
//   - rendering_density.md §1 dual-view mandate (BOTH buildExtended +
//     buildCompact REQUIRED; no opt-in)
//
// Design reference: mockups/Compact.png + mockups/Extended.png in this
// folder, committed alongside the module per the project's mockup-first
// workflow.
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No side
// effects, no global mutation, no register() calls.
//
// FM1 identity: the folder name `calculate_inflation_swap_curve_spread_tool`
// matches the backend-canonical MCP function name EXACTLY (no alias bridge
// needed — the catalog entry's "tool-name alias note" confirms).
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { InflationSwapCurveSpreadWidget } from './surfaces/monitor/InflationSwapCurveSpreadWidget';
import {
  ZCIS_CURVE_SPREAD_CURVE_OPTIONS,
  ZCIS_CURVE_SPREAD_TENORS_BY_CURVE,
} from './surfaces/inflationSwapCurveSpreadShared';

// Union of valid tenor labels across all three ZCIS curves.  The static
// catalog form layer does not (yet) support cross-field dynamic options,
// so we surface the UNION of tenors; tenors that are not valid for the
// chosen curve fall back gracefully in the widget (it snaps to the curve's
// first tenor pair).  Mirrors the breakeven_curve_spread module's
// triplet-options strategy.
const TENOR_UNION_OPTIONS = (() => {
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const tenors of Object.values(ZCIS_CURVE_SPREAD_TENORS_BY_CURVE)) {
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
  toolName: 'calculate_inflation_swap_curve_spread_tool',

  // FM3 — tier claims (parity with the calculate_breakeven_curve_spread_tool
  // shape-twin):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — same-curve ZCIS curve spreads (2s10s / 5s30s)
  //     are a desk daily-glance read for forward inflation curve shape
  //     (surface_contract.md §3.4).
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'ZCIS Curve Spread',
  category: 'cross_market_rv',
  oneLineSummary:
    'Same-curve zero-coupon inflation swap (ZCIS) tenor spread between two pillars of one ZCIS curve (e.g. USD 5s10s, EUR 5s30s, GBP 2s10s). Positive = upward-sloping forward inflation curve; negative = inverted. OTC ZCIS-implied forward inflation curve shape — distinct from bond-implied breakeven curve spread.',

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
  // §8).  Parameterised on (curve_family, short_tenor, long_tenor,
  // lookback_days); long > short enforced by the Pydantic strict-ordering
  // validator.  Fetches the SAME
  // /api/v1/rates/detail/inflation-swap-curve-spread endpoint.
  monitorWidgets: [
    {
      id: 'inflation_swap_curve_spread',
      label: 'ZCIS Curve Spread',
      description:
        'Same-curve ZCIS tenor spread (long − short) between two pillars of one ZCIS curve. OTC swap-implied forward inflation curve shape, separate from bond-implied breakeven curve.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family',
          label: 'ZCIS Curve',
          defaultValue: 'EUR_ZCIS',
          options: ZCIS_CURVE_SPREAD_CURVE_OPTIONS,
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
      component: InflationSwapCurveSpreadWidget,
    },
  ],

  // FM5 — defaults mirror the mockup (EUR 2s10s ZCIS · HICPxT) — the
  // committed Extended.png + Compact.png show this exact configuration.
  defaultParams: {
    curve_family: 'EUR_ZCIS',
    short_tenor: '2Y',
    long_tenor: '10Y',
    lookback_days: '365',
    field_name: 'PX_MID',
  },
};
