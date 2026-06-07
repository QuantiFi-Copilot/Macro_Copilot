// ============================================================================
// src/modules/primitives/scan_inflation_linkers_extremes_tool/module.ts
// ----------------------------------------------------------------------------
// Universe-wide linker REAL-YIELD extremes scanner under the dual-view +
// standalone-bridge contract.  Mirrors the scan_inflation_swaps_extremes
// scanner shape (ranked LIST of (curve_family, tenor) extremes by |z| of
// the rolling-252d real-yield LEVEL z-score, NOT a single time series).
// Compact view = top-N table; Extended view = universe-scan canvas
// (distribution histogram + ranked detail table); Monitor tile = bento
// flagged-count + top-3 ranked rows.
//
//   - methodology_exposure.md §5 standalone-bridge contract (own typed-
//     detail endpoint at /api/v1/rates/detail/linkers-scanner + own
//     surfaces; no shared typedView)
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
import { LinkerScannerWidget } from './surfaces/monitor/LinkerScannerWidget';
import { LINKER_SCANNER_CURVE_OPTIONS } from './surfaces/scanInflationLinkersExtremesShared';

// "ALL" sentinel for the Monitor scope dropdown — surfaces the full
// universe scan (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB
// together) as ONE option.  Mirrors the universe-scope convention used
// by the ZCIS scanner sibling.
const SCOPE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'ALL', label: 'All Linkers (TIPS / Gilts / OATi / RRB)' },
  ...LINKER_SCANNER_CURVE_OPTIONS,
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
  toolName: 'scan_inflation_linkers_extremes_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1).
  //     SCANNER shape: compact view is a top-N table, NOT a sparkline.
  //   * monitor_surface — universe-wide morning sweep is a desk-canonical
  //     glanceable read (surface_contract.md §3.4 eligibility — the desk's
  //     "where is the linker curve stretched today?" check is a daily
  //     ritual).
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Linker Universe Extremes',
  category: 'scanners',
  oneLineSummary:
    'Universe-wide linker real-yield sweep — ranks every (curve_family, tenor) linker pillar (USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB) by absolute 252-day rolling z-score of its real-yield LEVEL. Returns the top-N extremes with the load-bearing CPI-U / RPI / HICP / CAN-CPI INDEX-FAMILY + MARKET-STRUCTURE caveat. Morning screen, not a tactical trade signal.',

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
  // §8).  Fetches the SAME /api/v1/rates/detail/linkers-scanner endpoint
  // the Build views use.  The catalog form exposes three knobs:
  //   * scope          — 'ALL' OR a single linker curve family
  //   * top_n          — how many ranked extremes to surface
  //   * min_abs_z_score — flagging threshold
  monitorWidgets: [
    {
      id: 'linker_universe_extremes',
      label: 'Linker Extremes',
      description:
        'Universe-wide linker real-yield extremes (252d z-score). Surfaces the top-N flagged linker pillars across CPI-U / RPI / HICP / CAN-CPI — morning sweep tile.',
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
      component: LinkerScannerWidget,
    },
  ],

  // FM5 — defaults.  The scanner input is unique in that NO curve / tenor
  // is required — the default is "the full linker universe".  Top-N and
  // threshold default to the YAML's bundled conventions (5 / 1.5);
  // ``__ALL__`` is the magic sentinel BuildExtended resolves to "omit
  // curve_families ⇒ scan full universe".
  defaultParams: {
    curve_families: '__ALL__',
    top_n: '5',
    min_abs_z_score: '1.5',
  },
};
