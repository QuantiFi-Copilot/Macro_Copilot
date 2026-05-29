// ============================================================================
// src/modules/primitives/calculate_ois_butterfly_tool/module.ts
// ----------------------------------------------------------------------------
// Round-1 dispatch — brought to full parity with the
// calculate_inflation_swap_butterfly_tool sibling (single-curve 3-leg fly
// shape) and the calculate_real_yield_butterfly_tool sibling under the new
// standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/ois-butterfly + own surfaces; no shared
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
import { OisButterflyWidget } from './surfaces/monitor/OisButterflyWidget';
import {
  OIS_BUTTERFLY_CURVE_OPTIONS,
  OIS_BUTTERFLY_TRIPLETS_BY_CURVE,
} from './surfaces/oisButterflyShared';

// Triplet options for the Monitor widget — registered (short, belly, long)
// presets surfaced by label so the catalog form can dispatch only valid
// orderings.  The static catalog form layer does not (yet) support cross-
// field dynamic options, so we surface the UNION of valid triplet labels
// across all six OIS families (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS /
// JPY_OIS / AUD_OIS / CAD_OIS).  Triplets that are not valid for the user's
// selected curve fall back gracefully: the widget's fetchParams memo looks up
// the chosen label in OIS_BUTTERFLY_TRIPLETS_BY_CURVE[curve] and snaps to
// triplets[0] when the label is absent.  Default value '2s5s10s' is in every
// family's set so the default curve renders cleanly.
const BUTTERFLY_TRIPLET_OPTIONS = (() => {
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const triplets of Object.values(OIS_BUTTERFLY_TRIPLETS_BY_CURVE)) {
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
  toolName: 'calculate_ois_butterfly_tool',

  // FM3 — tier claims (parity with calculate_inflation_swap_butterfly_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — OIS butterflies are a desk-canonical curve-shape
  //     RV read on the front-end policy-expectations curve; surfacing them
  //     as a glanceable tile fits surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'OIS Butterfly',
  category: 'cross_market_rv',
  oneLineSummary:
    'Three-point OIS curve butterfly on a single OIS curve family (e.g. USD SOFR 2-5-10 OIS fly) with fixed (-1, +2, -1) weights. Positive = belly cheap; negative = belly rich. Curvature of the expected policy path under the risk-neutral measure — one curve, one overnight index (SOFR / ESTR / SONIA / TONA / AONIA / CORRA not fungible).',

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
  // §8).  ``curve`` is the OIS curve_family; ``triplet`` is the registered
  // (short, belly, long) tuple selected by label (e.g. "2s5s10s").  Fetches
  // the SAME /api/v1/rates/detail/ois-butterfly endpoint as the Build views.
  monitorWidgets: [
    {
      id: 'ois_butterfly',
      label: 'OIS Butterfly',
      description:
        'Three-point OIS butterfly on a single curve family ((2×belly − short − long) × 100). One curve, one overnight-index family (SOFR / ESTR / SONIA / TONA / AONIA / CORRA not fungible).',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve',
          label: 'OIS Curve',
          defaultValue: 'USD_SOFR_OIS',
          options: OIS_BUTTERFLY_CURVE_OPTIONS,
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
      component: OisButterflyWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic schema
  // requires.  Mockup default: USD_SOFR_OIS 2-5-10 (SOFR policy-curvature
  // focus).  ``field_name`` defaults to 'PX_LAST' — the YAML's
  // default_swap_rate_field (NOT the sovereign 'YLD_YTM_MID' default).  No
  // z-score / min-periods / ddof exposed on this primitive — those are
  // YAML-locked.
  defaultParams: {
    curve_family: 'USD_SOFR_OIS',
    short_tenor: '2Y',
    belly_tenor: '5Y',
    long_tenor: '10Y',
    lookback_days: '365',
    field_name: 'PX_LAST',
  },
};
