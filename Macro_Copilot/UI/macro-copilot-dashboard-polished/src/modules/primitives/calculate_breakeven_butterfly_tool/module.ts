// ============================================================================
// src/modules/primitives/calculate_breakeven_butterfly_tool/module.ts
// ----------------------------------------------------------------------------
// Round-1 dispatch — brought to full parity with the
// calculate_breakeven_inflation_simple_tool reference under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/breakeven-butterfly + own surfaces; no shared
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
import { BreakevenButterflyWidget } from './surfaces/monitor/BreakevenButterflyWidget';
import {
  BREAKEVEN_BUTTERFLY_PAIR_OPTIONS,
  BREAKEVEN_BUTTERFLY_TRIPLETS_BY_PAIR,
} from './surfaces/breakevenButterflyShared';

// Triplet options for the Monitor widget — registered (short, belly, long)
// presets surfaced by label so the catalog form can dispatch only valid
// orderings.  The static catalog form layer does not (yet) support
// cross-field dynamic options, so we surface the UNION of valid triplet
// labels across all four linker pairs (USD_TIPS, GBP_LINKER,
// EUR_FR_LINKER, CAD_RRB).  Triplets that are not valid for the user's
// selected pair fall back gracefully: the widget's fetchParams memo
// looks up the chosen label in BREAKEVEN_BUTTERFLY_TRIPLETS_BY_PAIR[pair]
// and snaps to triplets[0] when the label is absent.  Default value
// '5s10s30s' is in the USD_TIPS set so the default pair renders cleanly.
const BUTTERFLY_TRIPLET_OPTIONS = (() => {
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const triplets of Object.values(BREAKEVEN_BUTTERFLY_TRIPLETS_BY_PAIR)) {
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
  toolName: 'calculate_breakeven_butterfly_tool',

  // FM3 — tier claims (parity with calculate_breakeven_inflation_simple_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — breakeven butterflies are a desk-canonical
  //     curve-shape RV read; surfacing them as a glanceable tile fits the
  //     surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Breakeven Butterfly',
  category: 'cross_market_rv',
  oneLineSummary:
    'Three-point curvature of the bond-implied breakeven curve on a single same-country pair (e.g. US 2s5s10s breakeven fly) with fixed 50-50 wing weights. Positive = belly cheap; negative = belly rich. Inflation compensation curvature, not pure expected-inflation curvature.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  ``pair`` is the linker curve_family that uniquely picks the same-
  // country nominal counterparty; ``triplet`` is the registered (short,
  // belly, long) tuple selected by label (e.g. "5s10s30s") so the form
  // can't dispatch an invalid ordering against the structural Pydantic
  // strict-ordering validator.  Fetches the SAME
  // /api/v1/rates/detail/breakeven-butterfly endpoint.
  monitorWidgets: [
    {
      id: 'breakeven_butterfly',
      label: 'Breakeven Butterfly',
      description:
        'Three-point curvature of the bond-implied breakeven curve (belly − ½(short + long)). Inflation compensation curvature, not pure expected inflation.',
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
          options: BREAKEVEN_BUTTERFLY_PAIR_OPTIONS,
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
      component: BreakevenButterflyWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic
  // schema requires (no exposed z-score conventions on this tool — the
  // butterfly schema keeps them YAML-only).
  defaultParams: {
    nominal_curve_family: 'UST',
    linker_curve_family: 'USD_TIPS',
    short_tenor: '5Y',
    belly_tenor: '10Y',
    long_tenor: '30Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
