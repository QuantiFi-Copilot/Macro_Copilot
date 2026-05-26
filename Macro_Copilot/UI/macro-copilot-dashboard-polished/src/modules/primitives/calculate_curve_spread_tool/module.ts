// ============================================================================
// src/modules/primitives/calculate_curve_spread_tool/module.ts — Stage 4d.
// ----------------------------------------------------------------------------
// Stage 4a — typed-view module: ``custom_build_surface`` ships at
// ``surfaces/BuildSurface.tsx`` and routes via
// ``MODULE.typedView = 'spread'`` through the context decoder's
// TOOL_TO_VIEW derivation.
//
// Stage 4d — Monitor catalog: this primitive contributes TWO widgets
// to the Monitor bento catalog:
//   * ``curve_spreads`` — pre-aggregated 2s10s slope monitor across
//                         G4 (UST, Bund, Gilt, JGB).
//   * ``spread_chart`` — parameterised single-pair curve-spread chart
//                        (user picks curve + short_tenor + long_tenor
//                        + lookback).
// Multi-variant ⇒ uses ``monitorWidgets`` (not ``surfaces.monitor``).
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import {
  CURVE_OPTIONS,
  LOOKBACK_OPTIONS,
  TENOR_OPTIONS,
} from '@/lib/monitorParamOptions';
import BuildSurface from './surfaces/BuildSurface';
import { CurveSpreadsWidget } from './surfaces/monitor/CurveSpreadsWidget';
import { SpreadChartWidget } from './surfaces/monitor/SpreadChartWidget';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_curve_spread_tool',
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],
  displayName: 'Curve Spread',
  category: 'curve_shape',
  oneLineSummary:
    'Basis-point spread between two tenors on the same sovereign yield curve, with a fixed 1-year rolling z-score and full chartable time series.',
  typedView: 'spread',
  workspaceLabel: 'Spread chart & history',
  surfaces: { build: BuildSurface },
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
};
