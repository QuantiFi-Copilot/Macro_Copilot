// ============================================================================
// src/modules/primitives/calculate_curve_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Migration dispatch — converted from the legacy typed-renderer pattern
// view + standalone-bridge contract under:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// Backward-compat lock (per MIGRATION_RULES §6): the two existing Monitor
// widgets — ``curve_spreads`` (pre-aggregated G4 2s10s slope monitor) and
// ``spread_chart`` (parameterised single-pair chart) — are preserved
// verbatim.  Their ids + paramFields names + default values are unchanged so
// dashboards that persisted these widgets keep rendering.
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
import { CurveSpreadsWidget } from './surfaces/monitor/CurveSpreadsWidget';
import { SpreadChartWidget } from './surfaces/monitor/SpreadChartWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_curve_spread_tool',

  // FM3 — tier claims (unchanged from the pre-migration set; the backend's
  // _PRIMITIVE_SPECS membership is unchanged so the tier set mirrors backend
  // reality):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — two preserved Monitor widgets (pre-aggregated +
  //     parameterised) — backward-compat lock
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Curve Spread',
  category: 'curve_shape',
  oneLineSummary:
    'Basis-point spread between two tenors on the same sovereign yield curve, with a fixed 1-year rolling z-score and full chartable time series.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widgets — PRESERVED VERBATIM per MIGRATION_RULES §6.
  // The ids (``curve_spreads``, ``spread_chart``) are the backward-compat
  // lock — dashboards persist these ids.  The paramFields field names +
  // default values are also unchanged so persisted dashboard configs hydrate
  // identically.
  monitorWidgets: [
    {
      id: 'curve_spreads',
      label: 'Curve Spreads',
      description:
        '2s10s slope across G4 curves — current spread, daily change, z-score, sparkline. Backed by calculate_curve_spread_tool.',
      category: 'data',
      defaultSize: 'medium',
      allowedSizes: ['medium'],
      parameterized: false,
      component: CurveSpreadsWidget,
    },
    {
      id: 'spread_chart',
      label: 'Curve Spread',
      description:
        'A custom curve-spread chart (e.g. UST 2s10s, Bund 5s30s) with rolling z-score band.',
      category: 'data',
      defaultSize: 'medium',
      allowedSizes: ['medium', 'tall'],
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
          name: 'short_tenor',
          label: 'Short tenor',
          defaultValue: '2Y',
          options: TENOR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'long_tenor',
          label: 'Long tenor',
          defaultValue: '10Y',
          options: TENOR_OPTIONS,
          mustDifferFrom: 'short_tenor',
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: SpreadChartWidget,
    },
  ],

  // FM5 — defaults mirror the structural inputs the backend Pydantic schema
  // requires.  Mockup default: UST 2s10s (sovereign-canonical slope focus).
  // ``field_name`` defaults to 'YLD_YTM_MID' — the YAML's
  // default_field_name (mid yield-to-maturity).
  defaultParams: {
    curve_family: 'UST',
    short_tenor: '2Y',
    long_tenor: '10Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
