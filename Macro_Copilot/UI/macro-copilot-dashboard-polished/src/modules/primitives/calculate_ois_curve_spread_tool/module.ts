// ============================================================================
// src/modules/primitives/calculate_ois_curve_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Round-1 dispatch — brought to full parity with the
// calculate_real_yield_curve_spread_tool design twin (single-curve 2-leg
// tenor spread shape) and the OIS-family sibling calculate_ois_butterfly_tool
// (same closed curve_family enum, same risk-neutral policy-pricing caveat)
// under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/ois-curve-spread + own surfaces; no shared
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
import { OisCurveSpreadWidget } from './surfaces/monitor/OisCurveSpreadWidget';
import {
  OIS_CURVE_OPTIONS,
  OIS_CURVE_SPREAD_PAIRS_BY_CURVE,
} from './surfaces/oisCurveSpreadShared';

// Pair options for the Monitor widget — registered (short, long) presets
// surfaced by label so the catalog form can dispatch only valid orderings.
// The static catalog form layer does not (yet) support cross-field dynamic
// options, so we surface the UNION of valid pair labels across all six OIS
// families (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS / AUD_OIS /
// CAD_OIS).  Pairs that are not valid for the user's selected curve fall
// back gracefully: the widget's fetchParams memo looks up the chosen label
// in OIS_CURVE_SPREAD_PAIRS_BY_CURVE[curve] and snaps to pairs[0] when the
// label is absent.  Default value '2s10s' is in every family's set so the
// default curve renders cleanly.
const OIS_CURVE_SPREAD_PAIR_OPTIONS = (() => {
  const seen = new Set<string>();
  const out: { value: string; label: string }[] = [];
  for (const pairs of Object.values(OIS_CURVE_SPREAD_PAIRS_BY_CURVE)) {
    for (const p of pairs) {
      if (seen.has(p.label)) continue;
      seen.add(p.label);
      out.push({ value: p.label, label: p.label });
    }
  }
  return out;
})();

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_ois_curve_spread_tool',

  // FM3 — tier claims (parity with calculate_real_yield_curve_spread_tool +
  // calculate_ois_butterfly_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — OIS curve spreads are the canonical "where does
  //     the OIS curve sit?" desk read on the front-end policy-expectations
  //     curve; a Monitor tile fits surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'OIS Curve Spread',
  category: 'curve_shape',
  oneLineSummary:
    'Basis-point spread between two tenors on the same OIS curve family (e.g. USD SOFR 2s10s, EUR ESTR 1s5s) with rolling 252-day z-score. Shape of the risk-neutral expected policy path — one curve, one overnight index (SOFR / ESTR / SONIA / TONA / AONIA / CORRA not fungible).',

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
  // §8).  ``curve`` is the OIS curve_family; ``pair`` is the registered
  // (short, long) tuple selected by label (e.g. "2s10s").  Fetches the
  // SAME /api/v1/rates/detail/ois-curve-spread endpoint as the Build views.
  monitorWidgets: [
    {
      id: 'ois_curve_spread',
      label: 'OIS Curve Spread',
      description:
        'Two-point OIS tenor spread on a single curve family ((long − short) × 100, in bps). One curve, one overnight-index family (SOFR / ESTR / SONIA / TONA / AONIA / CORRA not fungible).',
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
          options: OIS_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'pair',
          label: 'Pair',
          defaultValue: '2s10s',
          options: OIS_CURVE_SPREAD_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: OisCurveSpreadWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic schema
  // requires.  Mockup default: USD_SOFR_OIS 2s10s (SOFR policy-shape focus).
  // ``field_name`` defaults to 'PX_LAST' — the YAML's
  // default_swap_rate_field (NOT the sovereign 'YLD_YTM_MID' default).
  // No z-score / min-periods / ddof exposed on this primitive — those are
  // YAML-locked (mirrors the sibling OIS butterfly bridge).
  defaultParams: {
    curve_family: 'USD_SOFR_OIS',
    short_tenor: '2Y',
    long_tenor: '10Y',
    lookback_days: '365',
    field_name: 'PX_LAST',
  },
};
