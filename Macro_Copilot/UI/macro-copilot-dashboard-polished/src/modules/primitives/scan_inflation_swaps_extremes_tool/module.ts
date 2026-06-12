// ============================================================================
// src/modules/primitives/scan_inflation_swaps_extremes_tool/module.ts
// ----------------------------------------------------------------------------
// First SCANNER-shape primitive shipped under the dual-view + standalone-
// bridge contract.  Wire output is a ranked LIST of (curve_family, tenor)
// extremes by |z| of the rolling-252d ZCIS rate LEVEL z-score, NOT a single
// time series — so this module's compact view is a top-N TABLE and its
// extended view is a universe-scan canvas (distribution histogram + ranked
// detail table), distinct from the level/spread/butterfly module shapes
// already in the catalogue.
//
//   - methodology_exposure.md §5 standalone-bridge contract (own typed-detail
//     endpoint at /api/v1/rates/detail/zcis-scanner + own surfaces; no
//   - rendering_density.md §1 dual-view mandate (BOTH buildExtended +
//     buildCompact REQUIRED; no opt-in)
//
// Design reference: mockups/Compact.png + mockups/Extended.png in this
// folder.
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No side
// effects, no global mutation, no register() calls.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { ZcisScannerWidget } from './surfaces/monitor/ZcisScannerWidget';
import { ZCIS_SCANNER_CURVE_OPTIONS } from './surfaces/zcisScannerShared';

// "ALL" sentinel for the Monitor scope dropdown — surfaces the full
// universe scan (USD_ZCIS / EUR_ZCIS / GBP_ZCIS together) as ONE option.
// Mirrors the universe-scope convention the catalog form expects when no
// curve_family filter is intended.
const SCOPE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'ALL', label: 'All ZCIS (USD / EUR / GBP)' },
  ...ZCIS_SCANNER_CURVE_OPTIONS,
];

const TOP_N_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '5', label: 'Top 5' },
  { value: '10', label: 'Top 10' },
  { value: '20', label: 'Top 20' },
];

const MIN_ABS_Z_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '1.0', label: '|z| ≥ 1.0' },
  { value: '1.5', label: '|z| ≥ 1.5' },
  { value: '2.0', label: '|z| ≥ 2.0' },
];

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'scan_inflation_swaps_extremes_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1).
  //     SCANNER shape: compact view is a top-N table, NOT a sparkline.
  //   * monitor_surface — universe-wide morning sweep is a desk-canonical
  //     glanceable read (surface_contract.md §3.4 eligibility — the desk's
  //     "where is ZCIS stretched today?" check is a daily ritual).
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'ZCIS Universe Extremes',
  category: 'scanners',
  oneLineSummary:
    'Universe-wide ZCIS rate-level sweep — ranks every (curve_family, tenor) ZCIS pillar (USD_ZCIS / EUR_ZCIS / GBP_ZCIS) by absolute 252-day rolling z-score of its quoted rate level. Returns the top-N extremes with the load-bearing CPI-U / HICPxT / RPI index-family caveat. Morning screen, not a tactical trade signal.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  Fetches the SAME /api/v1/rates/detail/zcis-scanner endpoint the
  // Build views use.  The catalog form exposes three knobs:
  //   * scope          — 'ALL' OR a single ZCIS curve family
  //   * top_n          — how many ranked extremes to surface
  //   * min_abs_z_score — flagging threshold
  monitorWidgets: [
    {
      id: 'zcis_universe_extremes',
      label: 'ZCIS Extremes',
      description:
        'Universe-wide ZCIS rate-level extremes (252d z-score). Surfaces the top-N flagged ZCIS pillars across CPI-U / HICPxT / RPI — morning sweep tile.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'scope',
          label: 'Scope',
          defaultValue: 'ALL',
          options: SCOPE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'top_n',
          label: 'Top N',
          defaultValue: '5',
          options: TOP_N_OPTIONS,
        },
        {
          kind: 'select',
          name: 'min_abs_z_score',
          label: 'Threshold',
          defaultValue: '1.5',
          options: MIN_ABS_Z_OPTIONS,
        },
      ],
      component: ZcisScannerWidget,
    },
  ],

  // FM5 — defaults.  The scanner input is unique in that NO curve / tenor
  // is required — the default is "the full ZCIS universe".  Top-N and
  // threshold default to the YAML's bundled conventions (5 / 1.5);
  // ``__ALL__`` is the magic sentinel BuildExtended resolves to "omit
  // curve_families ⇒ scan full universe".
  defaultParams: {
    curve_families: '__ALL__',
    top_n: '5',
    min_abs_z_score: '1.5',
  },
};
