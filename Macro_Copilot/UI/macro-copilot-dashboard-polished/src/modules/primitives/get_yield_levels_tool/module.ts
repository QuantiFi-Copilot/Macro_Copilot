// ============================================================================
// src/modules/primitives/get_yield_levels_tool/module.ts
// ----------------------------------------------------------------------------
// Migration dispatch — converted from the legacy typed-renderer pattern
// (``typedView: 'yield'`` + ``surfaces.resultRenderer``) to the new dual-
// view + standalone-bridge contract under:
//   - methodology_exposure.md §5 standalone-bridge (reuses the existing
//     typed-detail endpoint at /api/v1/rates/detail/yield + the existing
//     fetchDetailYield helper + the existing YieldLevelOutput type; no new
//     central infrastructure)
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// Backward-compat lock (MIGRATION_RULES §6): the parameterised
// ``yield_level`` Monitor widget is preserved verbatim — id + paramFields
// (curve_family, tenor, lookback_days) unchanged so dashboards that
// persisted this widget keep rendering.  The legacy pre-aggregated
// ``yield_snapshot`` widget in monitor/registry.ts is OUT OF SCOPE (it
// reads from the dashboard aggregate endpoint, not this tool).
//
// Design reference: mockups/Compact.png + mockups/Extended.png.
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import {
  CURVE_OPTIONS,
  LOOKBACK_OPTIONS,
  TENOR_OPTIONS,
} from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { YieldLevelWidget } from './surfaces/monitor/YieldLevelWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'get_yield_levels_tool',

  // FM3 — tier claims (unchanged from the pre-migration set; the backend's
  // _PRIMITIVE_SPECS membership is unchanged so the tier set mirrors backend
  // reality):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — preserved YieldLevelWidget tile (backward-compat lock)
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Yield Levels',
  category: 'snapshots',
  oneLineSummary:
    'Single-tenor sovereign yield snapshot — current yield, daily / weekly / monthly change in bps, rolling 252-day z-score, trailing 252-day high / low / percentile, observation count, and full chartable time series.',

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

  // FM5c — Monitor catalog widget — PRESERVED VERBATIM per MIGRATION_RULES §6.
  // The id (``yield_level``) is the backward-compat lock — dashboards
  // persist this id.  The paramFields field names + default values are
  // also unchanged so persisted dashboard configs hydrate identically.
  monitorWidgets: [
    {
      id: 'yield_level',
      label: 'Yield Level',
      description:
        'A single yield (curve × tenor) with daily change, z-score, and a 252-day sparkline.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family',
          label: 'Curve',
          defaultValue: 'UST',
          options: CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'tenor',
          label: 'Tenor',
          defaultValue: '10Y',
          options: TENOR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: YieldLevelWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic schema
  // requires.  Mockup default: UST 10Y (sovereign-canonical snapshot
  // focus).  ``field_name`` defaults to 'YLD_YTM_MID' — the YAML's
  // default_field_name (mid yield-to-maturity).
  defaultParams: {
    curve_family: 'UST',
    tenor: '10Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
