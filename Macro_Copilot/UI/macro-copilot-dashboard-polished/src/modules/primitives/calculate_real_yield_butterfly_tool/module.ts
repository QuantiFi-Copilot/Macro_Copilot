// ============================================================================
// src/modules/primitives/calculate_real_yield_butterfly_tool/module.ts
// ----------------------------------------------------------------------------
// Round-1 dispatch — brought to full parity with the
// calculate_breakeven_butterfly_tool sibling (3-leg fly shape) and the
// canonical calculate_breakeven_inflation_simple_tool reference under the
// new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/real-yield-butterfly + own surfaces; no shared
//     typedView)
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
import { RealYieldButterflyWidget } from './surfaces/monitor/RealYieldButterflyWidget';
import {
  REAL_YIELD_BUTTERFLY_CURVE_OPTIONS,
  REAL_YIELD_BUTTERFLY_TRIPLETS_BY_CURVE,
} from './surfaces/realYieldButterflyShared';

// Triplet options for the Monitor widget — registered (short, belly, long)
// presets surfaced by label so the catalog form can dispatch only valid
// orderings.  The static catalog form layer does not (yet) support
// cross-field dynamic options, so we surface the UNION of valid triplet
// labels across all four linker curves (USD_TIPS, GBP_LINKER,
// EUR_FR_LINKER, CAD_RRB).  Triplets that are not valid for the user's
// selected curve fall back gracefully: the widget's fetchParams memo
// looks up the chosen label in REAL_YIELD_BUTTERFLY_TRIPLETS_BY_CURVE[curve]
// and snaps to triplets[0] when the label is absent.  Default value
// '5s10s30s' is in the USD_TIPS set so the default curve renders cleanly.
const BUTTERFLY_TRIPLET_OPTIONS = (() => {
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const triplets of Object.values(REAL_YIELD_BUTTERFLY_TRIPLETS_BY_CURVE)) {
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
  toolName: 'calculate_real_yield_butterfly_tool',

  // FM3 — tier claims (parity with calculate_breakeven_butterfly_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — real-yield butterflies are a desk-canonical
  //     curve-shape RV read on the linker side; surfacing them as a
  //     glanceable tile fits the surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Real Yield Butterfly',
  category: 'cross_market_rv',
  oneLineSummary:
    'Three-point curvature of the linker real-yield curve on a single curve_family (e.g. USD_TIPS 5s10s30s real-yield fly) with fixed 50-50 wing weights. Positive = belly cheap; negative = belly rich. Curvature of real yields, distinct from breakeven-curve or nominal-curve curvature.',

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
  // §8).  ``curve`` is the linker curve_family (single-curve primitive — no
  // nominal pair, distinct from breakeven-butterfly); ``triplet`` is the
  // registered (short, belly, long) tuple selected by label (e.g. "5s10s30s")
  // so the form can't dispatch an invalid ordering against the structural
  // Pydantic strict-ordering validator.  Fetches the SAME
  // /api/v1/rates/detail/real-yield-butterfly endpoint.
  monitorWidgets: [
    {
      id: 'real_yield_butterfly',
      label: 'Real Yield Butterfly',
      description:
        'Three-point curvature of the linker real-yield curve (belly − ½(short + long)). Single linker curve; curvature of real rates.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve',
          label: 'Linker curve',
          defaultValue: 'USD_TIPS',
          options: REAL_YIELD_BUTTERFLY_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'triplet',
          label: 'Triplet',
          defaultValue: '5s10s30s',
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
      component: RealYieldButterflyWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic
  // schema requires.  USD_TIPS 5s10s30s is the canonical US default
  // (no 2Y TIPS series in the substrate; 2s5s10s would yield a controlled
  // error envelope).  No z-score / min-periods / ddof exposed on this
  // primitive — those are YAML-locked.
  defaultParams: {
    curve_family: 'USD_TIPS',
    short_tenor: '5Y',
    belly_tenor: '10Y',
    long_tenor: '30Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
