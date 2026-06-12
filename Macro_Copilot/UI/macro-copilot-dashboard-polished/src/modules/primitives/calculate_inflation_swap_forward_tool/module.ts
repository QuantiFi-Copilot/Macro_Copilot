// ============================================================================
// src/modules/primitives/calculate_inflation_swap_forward_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view + monitor module under the new standards:
//   - methodology_exposure.md §5 standalone-bridge contract (this tool
//     ships its own typed-detail endpoint at
//     /api/v1/rates/detail/inflation-swap-forward + its own frontend
//   - rendering_density.md §1 dual-view mandate (BOTH buildExtended +
//     buildCompact REQUIRED; no opt-in)
//
// Design reference: mockups/Compact.png + mockups/Extended.png in this
// folder, committed alongside the module per the project's mockup-first
// workflow.
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No
// side effects, no global mutation, no register() calls.
//
// FM1 identity note: the folder name `calculate_inflation_swap_forward_tool`
// matches the MCP function + workflow registry name exactly — no alias
// bridge needed (mirrors the sibling calculate_ois_forward_rate_tool /
// calculate_inflation_swap_rate_level_tool / calculate_inflation_swap_curve_spread_tool).
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import {
  ZCIS_CURVE_OPTIONS,
  ZCIS_FORWARD_PAIR_OPTIONS,
} from './surfaces/inflationSwapForwardShared';
import { InflationSwapForwardWidget } from './surfaces/monitor/InflationSwapForwardWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_inflation_swap_forward_tool',

  // FM3 — tier claims.
  //   * runtime status: generic_runnable (backend ships in _PRIMITIVE_SPECS)
  //   * custom_build_surface — the dual-view Build contract
  //     (rendering_density.md §1: both buildExtended + buildCompact)
  //   * monitor_surface — desk-glanceable per surface_contract.md §3.4
  //     (ZCIS forwards are the canonical long-run market read on forward
  //     inflation compensation — 5Y5Y ZCIS is the secular inflation
  //     anchor the desk parks on morning-briefing boards)
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Inflation Swap Forward',
  category: 'forward_rate',
  oneLineSummary:
    'Forward zero-coupon inflation swap rate spanning two pillars on the same ZCIS curve (e.g. USD_ZCIS 5Y5Y, EUR_ZCIS 5Y5Y, GBP_ZCIS 2Y3Y) via the dual-compounding geometric formula.  Output is FORWARD INFLATION COMPENSATION (not a clean forward expected-inflation read — ZCIS still carries an inflation risk premium and a smaller liquidity premium).  Carries forward_zcis_pct + 1D / 5D / 1M change in bps + rolling 252d z-score + trailing 252d high/low/percentile + the two endpoint ZCIS rates so the desk can audit the decomposition end-to-end.',

  // own full Build surfaces.

  // FM8 — surface refs (rendering_density.md §5):
  //   * buildExtended — full canvas, mounted for single-tool queries
  //   * buildCompact  — grid card, mounted as a node in multi-tool DAGs
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Per
  // docs_revamped/03_standards/rendering_density.md §8 the Monitor
  // surface is INHERENTLY COMPACT (no separate compact/extended split);
  // the widget renders at small/medium sizes inside the bento grid.
  // Parameterised on (curve_family, forward_pair, lookback_days); fetches
  // the SAME typed-detail endpoint
  // (/api/v1/rates/detail/inflation-swap-forward) per the standalone-
  // bridge contract in methodology_exposure.md §5.4.
  monitorWidgets: [
    {
      id: 'inflation_swap_forward',
      label: 'ZCIS Forward Rate',
      description:
        'ZCIS implied forward rate (level + z-score + 252d range) for one (curve_family, forward_pair) — e.g. EUR_ZCIS 5Y5Y.  Forward inflation compensation read; index-family caveat applies.',
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
          options: ZCIS_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'forward_pair',
          label: 'Forward',
          defaultValue: '5Y5Y',
          options: ZCIS_FORWARD_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: InflationSwapForwardWidget,
    },
  ],

  // FM5 — defaults.  Mockup default: EUR_ZCIS 5Y5Y (the 5Y5Y HICPxT
  // forward is the long-run inflation-compensation anchor the ECB watches
  // explicitly; USD_ZCIS 5Y5Y is its US analogue, GBP_ZCIS 5Y5Y the UK).
  // start_tenor / end_tenor are also pre-set so the backend's tenor-pair
  // input accepts the params dict directly (the Build surfaces re-derive
  // both from forward_pair on change, keeping them in sync).
  defaultParams: {
    curve_family: 'EUR_ZCIS',
    forward_pair: '5Y5Y',
    start_tenor: '5Y',
    end_tenor: '10Y',
    lookback_days: '365',
    field_name: 'PX_MID',
  },
};
