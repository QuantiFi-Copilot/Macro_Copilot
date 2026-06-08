// ============================================================================
// src/modules/primitives/calculate_inflation_swap_rate_level_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view + monitor module under the new standards:
//   - methodology_exposure.md §5 standalone-bridge contract (this tool
//     ships its own typed-detail endpoint at
//     /api/v1/rates/detail/inflation-swap-rate-level + its own frontend
//     surfaces; no shared typedView reuse)
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
// FM1 identity note: the folder name `calculate_inflation_swap_rate_level_tool`
// matches the backend-canonical MCP function name exactly (no alias bridge
// needed — the catalog entry's "tool-name alias note" confirms).
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import {
  ZCIS_CURVE_OPTIONS,
  ZCIS_DEFAULT_TENORS,
} from './surfaces/inflationSwapRateLevelShared';
import { InflationSwapRateLevelWidget } from './surfaces/monitor/InflationSwapRateLevelWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName).  No alias bridge needed —
  // the catalog entry explicitly notes the MCP function name and the
  // folder name match.
  toolName: 'calculate_inflation_swap_rate_level_tool',

  // FM3 — tier claims.
  //   * runtime status: generic_runnable (backend ships in _PRIMITIVE_SPECS)
  //   * custom_build_surface — the dual-view Build contract
  //     (rendering_density.md §1: both buildExtended + buildCompact)
  //   * monitor_surface — desk-glanceable per surface_contract.md §3.4
  //     (ZCIS rates are a canonical daily PM read — the cleanest market
  //     read on forward inflation expectations, separate from bond-implied
  //     breakeven)
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'ZCIS Rate Level',
  category: 'snapshots',
  oneLineSummary:
    'Single-pillar zero-coupon inflation swap (ZCIS) rate snapshot — current rate, daily / weekly / monthly change in bps, rolling 252-day z-score, trailing 252-day high / low / percentile, and a full chartable time series.  Pure inflation-compensation read separate from bond-implied breakeven.  ZCIS analogue of get_ois_rate_level.',

  // FM9 — routing claims.  STANDALONE pattern per methodology_exposure.md §5:
  // no shared typedView.  The module owns its own full Build surfaces.
  typedView: null,
  richModel: false,

  // FM8 — surface refs (rendering_density.md §5):
  //   * buildExtended — full canvas, mounted for single-tool queries
  //   * buildCompact  — grid card, mounted as a node in multi-tool DAGs
  surfaces: {
    build: BuildExtended,
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Per
  // docs_revamped/03_standards/rendering_density.md §8 the Monitor surface
  // is INHERENTLY COMPACT (no separate compact/extended split); the widget
  // renders at small/medium sizes inside the bento grid.  Parameterised on
  // (curve_family, tenor, lookback_days) — same inputs the Build surfaces
  // use; fetches the SAME typed-detail endpoint
  // (/api/v1/rates/detail/inflation-swap-rate-level) per the standalone-
  // bridge contract in methodology_exposure.md §5.4.
  monitorWidgets: [
    {
      id: 'inflation_swap_rate_level',
      label: 'ZCIS Rate Level',
      description:
        'ZCIS par rate (level + z-score + 252d range) for one curve pillar. Cleanest market read on forward inflation expectations — separate from bond-implied breakeven.',
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
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '5Y',
          options: ZCIS_DEFAULT_TENORS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: InflationSwapRateLevelWidget,
    },
  ],

  // FM5 — defaults.  Mockup default: EUR_ZCIS 5Y (HICPxT 5Y is the
  // desk-canonical Eurozone inflation-compensation read; matches the
  // committed Compact.png + Extended.png mockups).
  defaultParams: {
    curve_family: 'EUR_ZCIS',
    tenor: '5Y',
    lookback_days: '365',
    field_name: 'PX_MID',
  },
};
