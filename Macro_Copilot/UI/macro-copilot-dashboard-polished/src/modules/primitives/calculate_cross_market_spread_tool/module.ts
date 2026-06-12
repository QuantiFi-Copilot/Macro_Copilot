// ============================================================================
// src/modules/primitives/calculate_cross_market_spread_tool/module.ts
// ----------------------------------------------------------------------------
// Migration dispatch — converted from the legacy typed-renderer pattern
// dual-view + standalone-bridge contract under:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// Backward-compat lock (per MIGRATION_RULES §6): the two existing Monitor
// widgets — ``cross_market_spreads`` (pre-aggregated G3 cross-sovereign
// dashboard) and ``cross_market_spread`` (parameterised single-pair chart) —
// are preserved verbatim.  Their ids + paramFields names + default values
// are unchanged so dashboards that persisted these widgets keep rendering.
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
import { CrossMarketSpreadsWidget } from './surfaces/monitor/CrossMarketSpreadsWidget';
import { CrossMarketSpreadWidget } from './surfaces/monitor/CrossMarketSpreadWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_cross_market_spread_tool',

  // FM3 — tier claims (unchanged from the pre-migration set; the backend's
  // _PRIMITIVE_SPECS membership is unchanged so the tier set mirrors backend
  // reality):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — two preserved Monitor widgets (pre-aggregated +
  //     parameterised) — backward-compat lock
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Cross Market Spread',
  category: 'cross_market_rv',
  oneLineSummary:
    'Yield differential between the same tenor on two sovereign curves (e.g. BTP-Bund 10Y), in basis points, with rolling 252-day z-score, daily / weekly / monthly change, trailing range, and full time series.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widgets — PRESERVED VERBATIM per MIGRATION_RULES §6.
  // The ids (``cross_market_spreads``, ``cross_market_spread``) are the
  // backward-compat lock — dashboards persist these ids.  The paramFields
  // field names + default values are also unchanged so persisted dashboard
  // configs hydrate identically.
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

  // FM5 — defaults mirror the structural inputs the backend Pydantic schema
  // requires.  Mockup default: UST-Bund 10Y (transatlantic sovereign-rate
  // differential — the desk-canonical cross-market spread).  ``field_name``
  // defaults to 'YLD_YTM_MID' — the YAML's default_field_name (mid
  // yield-to-maturity).
  defaultParams: {
    curve_family_1: 'UST',
    curve_family_2: 'DE_BUND',
    tenor: '10Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
