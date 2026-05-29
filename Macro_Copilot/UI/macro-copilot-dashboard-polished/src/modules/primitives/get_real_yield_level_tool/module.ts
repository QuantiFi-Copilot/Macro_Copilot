// ============================================================================
// src/modules/primitives/get_real_yield_level_tool/module.ts
// ----------------------------------------------------------------------------
// Phase-1 pilot tool under the new standards:
//   - methodology_exposure.md §5 standalone-bridge contract (this tool
//     ships its own typed-detail endpoint at /api/v1/rates/detail/real_yield
//     + its own frontend surfaces; no shared typedView reuse)
//   - rendering_density.md §1 dual-view mandate (BOTH buildExtended +
//     buildCompact REQUIRED; no opt-in)
//
// Design reference: mockups/Compact.png + mockups/Extended.png in this
// folder, committed alongside the module per the project's mockup-first
// workflow.
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No
// side effects, no global mutation, no register() calls.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import {
  LINKER_CURVE_OPTIONS,
  LINKER_DEFAULT_TENORS,
  LOOKBACK_OPTIONS,
} from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { RealYieldLevelWidget } from './surfaces/monitor/RealYieldLevelWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'get_real_yield_level_tool',

  // FM3 — tier claims.
  //   * runtime status: generic_runnable (backend ships in _PRIMITIVE_SPECS)
  //   * custom_build_surface — the dual-view Build contract
  //     (rendering_density.md §1: both buildExtended + buildCompact)
  //   * monitor_surface — desk-glanceable per surface_contract.md §3.4
  //     (real yields are a canonical daily PM read)
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Real Yield Level',
  category: 'snapshots',
  oneLineSummary:
    'Single-tenor sovereign-linker real-yield snapshot — current yield, daily / weekly / monthly change in bps, rolling 252-day z-score (override-tunable per call), trailing 252-day high / low / percentile, and full chartable time series. Linker analogue of get_yield_levels.',

  // FM9 — routing claims.  STANDALONE pattern per methodology_exposure.md §5:
  // no shared typedView.  The module owns its own full Build surfaces.
  typedView: null,
  richModel: false,

  // FM8 — surface refs (rendering_density.md §5):
  //   * buildExtended — full canvas, mounted for single-tool queries
  //   * buildCompact  — grid card, mounted as a node in multi-tool DAGs
  //
  // ``build`` is set to ``BuildExtended`` for backward compat with the
  // existing VirtualPrimitiveCanvas dispatcher (which currently reads
  // ``surfaces.build``).  Once the dispatcher reads ``buildExtended``
  // first, the ``build`` field is removed for new modules.
  surfaces: {
    build: BuildExtended,
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Per docs_revamped/03_standards/rendering_density.md §8
  // the Monitor surface is INHERENTLY COMPACT (no separate compact/extended
  // split); the widget renders at small/medium sizes inside the bento
  // grid.  Parameterised on (curve_family, tenor, lookback_days) — same
  // inputs the Build surfaces use; fetches the SAME typed-detail
  // endpoint (/api/v1/rates/detail/real_yield) per the standalone-bridge
  // contract in methodology_exposure.md §5.4.
  monitorWidgets: [
    {
      id: 'real_yield_level',
      label: 'Real Yield Level',
      description:
        'Real yield (level + z-score + 252d range) for one sovereign-linker curve point. Linker analogue of the Yield Level widget.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family',
          label: 'Linker curve',
          defaultValue: 'USD_TIPS',
          options: LINKER_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '10Y',
          options: LINKER_DEFAULT_TENORS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: RealYieldLevelWidget,
    },
  ],

  // FM5 — defaults + paramHints + interpretationCards.
  // Mirrors Stage 1A exposure decisions in
  // rates_agent/inflation_indexed_bonds/tools/real_yield_level/config.yaml:
  //   exposed: curve_family, tenor, lookback_days, field_name +
  //            z_score_window_days, z_score_min_periods, z_score_ddof.
  defaultParams: {
    curve_family: 'USD_TIPS',
    tenor: '10Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
