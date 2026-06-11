// ============================================================================
// src/modules/primitives/calculate_zscore_custom_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view module under the new standards (parity with the
// calculate_breakeven_inflation_simple_tool pilot):
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/zscore-custom — already live on the
//     backend — + own surfaces; no shared typedView)
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { CURVE_OPTIONS, TENOR_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { ZScoreCustomWidget } from './surfaces/monitor/ZScoreCustomWidget';
import { ZSCORE_WINDOW_OPTIONS } from './surfaces/zscoreCustomShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_zscore_custom_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — a stretch read at a chosen horizon is desk-
  //     glanceable; the registry carries NO custom-window z widget today
  //     (yield_snapshot + yield_level both pin the FIXED 252d window), so
  //     this tile is differentiated, not duplicative.  THESIS Q3.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata (kept from the Stage-3 scaffold)
  displayName: 'Z-Score Custom',
  category: 'rolling_analytics',
  oneLineSummary: 'Rolling z-score of a single sovereign yield with a user-supplied window length (vs the fixed 252-day window in get_yield_levels). Returns current z-score, latest yield, actual window parameters, and full z-score time series.',

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
  // §8).  ``z_score_window_days`` is the differentiating param — the desk
  // pins a stretch read at a chosen horizon (60d tactical / 252d annual /
  // 504d two-year).  Fetches the SAME /api/v1/rates/detail/zscore-custom
  // endpoint the Build views use.
  monitorWidgets: [
    {
      id: 'zscore_custom',
      label: 'Custom-Window Z-Score',
      description:
        'Rolling z-score of one sovereign yield at a user-chosen window length (60d tactical / 252d annual / …) with regime banding. Stretch diagnostic, not a directional signal.',
      category: 'analysis',
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
          name: 'z_score_window_days',
          label: 'Z window',
          defaultValue: '252',
          options: ZSCORE_WINDOW_OPTIONS,
        },
      ],
      component: ZScoreCustomWidget,
    },
  ],

  // FM5 — defaults mirror the backend Input surface (A13: only
  // curve_family / tenor / z_score_window_days / lookback_days /
  // field_name are exposed; min_periods + ddof are YAML-locked and have
  // no input fields).  field_name is intentionally OMITTED so the
  // backend's default_field_name convention flows through (wrapper-
  // shadowing fix, commit b2605ee).
  defaultParams: {
    curve_family: 'UST',
    tenor: '10Y',
    z_score_window_days: '252',
    lookback_days: '365',
  },
};
