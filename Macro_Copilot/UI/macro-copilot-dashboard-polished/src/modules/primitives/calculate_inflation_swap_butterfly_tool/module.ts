// ============================================================================
// src/modules/primitives/calculate_inflation_swap_butterfly_tool/module.ts
// ----------------------------------------------------------------------------
// Round-1 dispatch — brought to full parity with the
// calculate_real_yield_butterfly_tool sibling (single-curve 3-leg fly shape)
// and the calculate_cross_market_inflation_swap_spread_tool sibling
// (CPI-family caveat threading) under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/zcis-butterfly + own surfaces; no shared
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
import { InflationSwapButterflyWidget } from './surfaces/monitor/InflationSwapButterflyWidget';
import {
  INFLATION_SWAP_BUTTERFLY_CURVE_OPTIONS,
  INFLATION_SWAP_BUTTERFLY_TRIPLETS_BY_CURVE,
} from './surfaces/inflationSwapButterflyShared';

// Triplet options for the Monitor widget — registered (short, belly, long)
// presets surfaced by label so the catalog form can dispatch only valid
// orderings.  The static catalog form layer does not (yet) support
// cross-field dynamic options, so we surface the UNION of valid triplet
// labels across all three ZCIS families (USD_ZCIS / EUR_ZCIS / GBP_ZCIS).
// Triplets that are not valid for the user's selected curve fall back
// gracefully: the widget's fetchParams memo looks up the chosen label in
// INFLATION_SWAP_BUTTERFLY_TRIPLETS_BY_CURVE[curve] and snaps to
// triplets[0] when the label is absent.  Default value '2s5s10s' is in
// every family's set so the default curve renders cleanly.
const BUTTERFLY_TRIPLET_OPTIONS = (() => {
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const triplets of Object.values(INFLATION_SWAP_BUTTERFLY_TRIPLETS_BY_CURVE)) {
    for (const t of triplets) {
      if (seen.has(t.label)) continue;
      seen.add(t.label);
      out.push({ value: t.label, label: t.label });
    }
  }
  return out;
})();

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_inflation_swap_butterfly_tool',

  // FM3 — tier claims (parity with calculate_real_yield_butterfly_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — ZCIS butterflies are a desk-canonical curve-
  //     shape RV read on the inflation-swap side; surfacing them as a
  //     glanceable tile fits surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Inflation Swap Butterfly',
  category: 'cross_market_rv',
  oneLineSummary:
    'Three-point ZCIS butterfly on a single ZCIS curve family (e.g. USD ZCIS 2-5-10 fly) with fixed (-0.5, +1.0, -0.5) weights. Positive = belly cheap; negative = belly rich. Curvature of zero-coupon inflation swap rates — one curve, one inflation index (CPI-U / HICPxT / RPI not fungible).',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  ``curve`` is the ZCIS curve_family; ``triplet`` is the registered
  // (short, belly, long) tuple selected by label (e.g. "2s5s10s") so the
  // form can't dispatch an invalid ordering against the structural Pydantic
  // strict-ordering validator.  Fetches the SAME
  // /api/v1/rates/detail/zcis-butterfly endpoint.
  monitorWidgets: [
    {
      id: 'inflation_swap_butterfly',
      label: 'Inflation Swap Butterfly',
      description:
        'Three-point ZCIS butterfly on a single curve family (belly − ½(short + long)). One curve, one inflation index family (CPI-U / HICPxT / RPI not fungible).',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve',
          label: 'ZCIS Curve',
          defaultValue: 'USD_ZCIS',
          options: INFLATION_SWAP_BUTTERFLY_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'triplet',
          label: 'Triplet',
          defaultValue: '2s5s10s',
          options: BUTTERFLY_TRIPLET_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: InflationSwapButterflyWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic schema
  // requires.  Mockup default: USD_ZCIS 2-5-10 (CPI-U term-structure
  // curvature focus).  ``field_name`` defaults to 'PX_MID' — the YAML's
  // default_zcis_rate_field.  No z-score / min-periods / ddof exposed on
  // this primitive — those are YAML-locked.
  defaultParams: {
    curve_family: 'USD_ZCIS',
    short_tenor: '2Y',
    belly_tenor: '5Y',
    long_tenor: '10Y',
    lookback_days: '365',
    field_name: 'PX_MID',
  },
};
