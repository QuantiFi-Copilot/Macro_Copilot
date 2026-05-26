// ============================================================================
// src/modules/primitives/calculate_cross_market_spread_tool/module.ts — Stage 4d.
// ----------------------------------------------------------------------------
// Stage 4a — typed-view module: ``custom_build_surface`` ships at
// ``surfaces/BuildSurface.tsx``; routes via
// ``MODULE.typedView = 'cross_market'``.
//
// Stage 4d — Monitor catalog: two widgets — a pre-aggregated G3
// cross-market dashboard and a parameterised single-pair chart.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import {
  CURVE_OPTIONS,
  LOOKBACK_OPTIONS,
  TENOR_OPTIONS,
} from '@/lib/monitorParamOptions';
import ResultRenderer from './surfaces/ResultRenderer';
import { CrossMarketSpreadsWidget } from './surfaces/monitor/CrossMarketSpreadsWidget';
import { CrossMarketSpreadWidget } from './surfaces/monitor/CrossMarketSpreadWidget';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_cross_market_spread_tool',
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],
  displayName: 'Cross Market Spread',
  category: 'cross_market_rv',
  oneLineSummary:
    'Yield differential between the same tenor on two sovereign curves (e.g. BTP-Bund 10Y), in basis points, with rolling 252-day z-score, daily / weekly / monthly change, trailing range, and full time series.',
  typedView: 'cross_market',
  workspaceLabel: 'Cross-market chart',
  surfaces: { resultRenderer: ResultRenderer },
  monitorWidgets: [
    {
      id: 'cross_market_spreads',
      label: 'Cross-Market Spreads',
      description:
        'Cross-sovereign spreads (BTP-Bund, OAT-Bund, UST-Bund) at the 10Y point, with daily / monthly change, percentile, z-score. Backed by calculate_cross_market_spread_tool.',
      category: 'analysis',
      defaultSize: 'medium',
      allowedSizes: ['medium'],
      parameterized: false,
      component: CrossMarketSpreadsWidget,
    },
    {
      id: 'cross_market_spread',
      label: 'Cross-Market Spread',
      description:
        'Custom cross-sovereign spread (e.g. BTP-Bund 10Y, OAT-Bund 10Y) with rolling z-score.',
      category: 'analysis',
      defaultSize: 'medium',
      allowedSizes: ['medium', 'tall'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family_1',
          label: 'Curve A',
          defaultValue: 'IT_BTP',
          options: CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'curve_family_2',
          label: 'Curve B',
          defaultValue: 'DE_BUND',
          options: CURVE_OPTIONS,
          mustDifferFrom: 'curve_family_1',
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
      component: CrossMarketSpreadWidget,
    },
  ],
};
