// ============================================================================
// src/modules/primitives/get_yield_levels_tool/module.ts — Stage 4d.
// ----------------------------------------------------------------------------
// Stage 4a — typed-view module: ``custom_build_surface`` ships at
// ``surfaces/BuildSurface.tsx``; routes via ``typedView = 'yield'``.
// Stage 4d — Monitor catalog: parameterised single-yield widget.
// (The pre-aggregated ``yield_snapshot`` widget in the legacy registry
// reads from the dashboard aggregate endpoint, NOT this tool, so it
// stays as a hand-authored entry in ``monitor/registry.ts``.)
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import {
  CURVE_OPTIONS,
  LOOKBACK_OPTIONS,
  TENOR_OPTIONS,
} from '@/lib/monitorParamOptions';
import ResultRenderer from './surfaces/ResultRenderer';
import { YieldLevelWidget } from './surfaces/monitor/YieldLevelWidget';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'get_yield_levels_tool',
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],
  displayName: 'Yield Levels',
  category: 'snapshots',
  oneLineSummary:
    'Single-tenor sovereign yield snapshot — current yield, daily / weekly / monthly change in bps, rolling 252-day z-score, trailing 252-day high / low / percentile, observation count, and full chartable time series.',
  typedView: 'yield',
  surfaces: { resultRenderer: ResultRenderer },
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
};
