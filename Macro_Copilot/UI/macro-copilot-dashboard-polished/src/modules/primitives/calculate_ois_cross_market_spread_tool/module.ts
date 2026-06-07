// ============================================================================
// src/modules/primitives/calculate_ois_cross_market_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Factory dispatch — brought to full parity with the
// calculate_cross_market_inflation_swap_spread_tool reference (the same-shape
// design twin — two-curve same-tenor cross-market spread on a different curve
// family) and the OIS-family siblings calculate_ois_curve_spread_tool /
// calculate_ois_butterfly_tool (same closed curve_family enum, same
// risk-neutral policy-pricing caveat) under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/ois-cross-market-spread + own surfaces; no
//     shared typedView)
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
import { CrossMarketOisWidget } from './surfaces/monitor/CrossMarketOisWidget';
import {
  OIS_CROSS_MARKET_TENOR_OPTIONS,
  OIS_FAMILY_OPTIONS,
} from './surfaces/crossMarketOisShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_ois_cross_market_spread_tool',

  // FM3 — tier claims (parity with calculate_cross_market_inflation_swap_spread_tool
  // + calculate_ois_curve_spread_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — cross-market OIS spreads are the canonical RV read
  //     on G4 policy-path divergence; surfacing them as glanceable tiles fits
  //     surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Cross-Market OIS Spread',
  category: 'cross_market_rv',
  oneLineSummary:
    'Same-tenor cross-market OIS spread (e.g. USD_SOFR_OIS 2Y minus EUR_ESTR_OIS 2Y) — daily/weekly/monthly bps changes, 252-day rolling z-score, trailing range. Canonical G4 read on relative central-bank policy stance; surfaces the risk-neutral policy-pricing caveat (SOFR / ESTR / SONIA / TONA / AONIA / CORRA are NOT fungible policy benchmarks).',

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
  // §8).  Two-curve, single-tenor primitive — exposes both legs + tenor +
  // lookback.  Fetches the SAME /api/v1/rates/detail/ois-cross-market-spread
  // endpoint the Build views use (standalone bridge §5.4).
  monitorWidgets: [
    {
      id: 'ois_cross_market_spread',
      label: 'Cross-Market OIS Spread',
      description:
        'Same-tenor cross-market OIS spread between two OIS curve families (e.g. USD_SOFR_OIS - EUR_ESTR_OIS 2Y). Risk-neutral policy-path divergence across G4 central banks (SOFR / ESTR / SONIA / TONA / AONIA / CORRA not fungible).',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family_1',
          label: 'Leg A',
          defaultValue: 'USD_SOFR_OIS',
          options: OIS_FAMILY_OPTIONS,
        },
        {
          kind: 'select',
          name: 'curve_family_2',
          label: 'Leg B',
          defaultValue: 'EUR_ESTR_OIS',
          options: OIS_FAMILY_OPTIONS,
        },
        {
          kind: 'select',
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '2Y',
          options: OIS_CROSS_MARKET_TENOR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: CrossMarketOisWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic schema
  // requires.  Mockup default: USD_SOFR_OIS vs EUR_ESTR_OIS 2Y (Fed vs ECB
  // 2Y policy-rate differential — the canonical desk read).  ``field_name``
  // defaults to 'PX_LAST' — the YAML's default_swap_rate_field (NOT the
  // sovereign 'YLD_YTM_MID' default).  No z-score / min-periods / ddof
  // exposed on this primitive — those are YAML-locked (mirrors the OIS
  // curve_spread / butterfly bridges).
  defaultParams: {
    curve_family_1: 'USD_SOFR_OIS',
    curve_family_2: 'EUR_ESTR_OIS',
    tenor: '2Y',
    lookback_days: '365',
    field_name: 'PX_LAST',
  },
};
