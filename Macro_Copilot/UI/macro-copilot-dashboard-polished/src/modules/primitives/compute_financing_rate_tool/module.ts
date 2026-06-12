// ============================================================================
// src/modules/primitives/compute_financing_rate_tool/module.ts
// ----------------------------------------------------------------------------
// Frontend module under the new standards:
//   - methodology_exposure.md §5 standalone-bridge contract (this tool
//     ships its own typed-detail endpoint at /api/v1/rates/detail/financing-rate
//   - rendering_density.md §1 dual-view mandate (BOTH buildExtended +
//     buildCompact REQUIRED)
//
// ARCHITECTURAL DEVIATION (documented in THESIS.md "Backend shape note"):
// the backend FinancingRateOutput is Panel-shaped (no current_metrics /
// time_series).  The route at /detail/financing-rate SYNTHESIZES the
// snapshot shape from result.panel.payload (Option (a) — human-authorized
// 2026-06-08).  Above the typed-detail boundary this module is
// structurally identical to other snapshot tools (reference:
// get_real_yield_level_tool).
//
// Design reference: mockups/Compact.png + mockups/Extended.png in this
// folder, committed alongside the module per the project's mockup-first
// workflow.
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No
// side effects, no global mutation, no register() calls.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { FinancingRateWidget } from './surfaces/monitor/FinancingRateWidget';

const PROXY_CURVE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'USD_SOFR_OIS', label: 'USD SOFR · UST proxy' },
  { value: 'EUR_ESTR_OIS', label: 'EUR €STR · Bund proxy' },
  { value: 'GBP_SONIA_OIS', label: 'GBP SONIA · Gilt proxy' },
  { value: 'JPY_TONA_OIS', label: 'JPY TONA · JGB proxy' },
  { value: 'AUD_AONIA_OIS', label: 'AUD AONIA · ACGB proxy' },
  { value: 'CAD_CORRA_OIS', label: 'CAD CORRA · CGB proxy' },
];

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'compute_financing_rate_tool',

  // FM3 — tier claims.
  //   * runtime status: generic_runnable (backend ships in _PRIMITIVE_SPECS)
  //   * custom_build_surface — dual-view Build contract per
  //     rendering_density.md §1 (both buildExtended + buildCompact)
  //   * monitor_surface — desk-glanceable per surface_contract.md §3.4
  //     (financing rate is a canonical daily PM read for repo / specials
  //     dislocations)
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Financing Rate',
  category: 'snapshots',
  oneLineSummary:
    'Daily OIS-implied financing rate at one proxy curve (e.g. USD_SOFR_OIS proxies UST financing).  Snapshot view: current rate, 1d/5d/1m changes in bps, rolling 252-day z-score, trailing 252-day high / low / percentile, and full chartable time series.  Backend Panel-shape is reduced to a snapshot shape by the route handler — see THESIS Backend shape note.',


  // FM8 — surface refs (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact per
  // rendering_density.md §8; renders at small / medium sizes inside the
  // bento grid.  Parameterised on (proxy_curve, lookback_days) — same
  // inputs the Build surfaces use; fetches the SAME synthesized typed-
  // detail endpoint (/api/v1/rates/detail/financing-rate) per the
  // standalone-bridge contract in methodology_exposure.md §5.4.
  monitorWidgets: [
    {
      id: 'financing_rate',
      label: 'Financing Rate',
      description:
        'OIS-implied financing rate (level + z-score + 252d range) for one proxy curve.  Repo / specials dislocation signal.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'proxy_curve',
          label: 'Proxy curve',
          defaultValue: 'USD_SOFR_OIS',
          options: PROXY_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: FinancingRateWidget,
    },
  ],

  // FM5 — defaults.  The bridge accepts ONLY (method, proxy_curve,
  // lookback_days) per the catalog entry; no tenor (financing is an
  // overnight quantity).
  defaultParams: {
    method: 'overnight_index_proxy',
    proxy_curve: 'USD_SOFR_OIS',
    lookback_days: '252',
  },
};
