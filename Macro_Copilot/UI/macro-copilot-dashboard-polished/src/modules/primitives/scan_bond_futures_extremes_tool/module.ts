// ============================================================================
// src/modules/primitives/scan_bond_futures_extremes_tool/module.ts
// ----------------------------------------------------------------------------
// Universe-wide bond-futures extremes scanner under the dual-view +
// standalone-bridge contract.  SCANNER shape (multi-metric) — the wire
// returns a ranked LIST of (curve_family, contract_code) extremes across
// FOUR metrics (price LEVEL, 1-day price CHANGE, volume LEVEL, open-
// interest LEVEL), each independently ranked by absolute 252d-rolling
// z-score.  Compact view = top-N table (sorted cross-metric by |z|);
// Extended view = universe-scan canvas (distribution histogram + multi-
// metric ranked detail table); Monitor tile = bento flagged-count + top-3
// cross-metric rows.
//
//   - methodology_exposure.md §5 standalone-bridge contract (own typed-
//     detail endpoint at /api/v1/rates/detail/bond-futures-scanner +
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
import { BondFuturesScannerWidget } from './surfaces/monitor/BondFuturesScannerWidget';
import { BOND_FUTURES_SCANNER_CURVE_OPTIONS } from './surfaces/bondFuturesScannerShared';

// "ALL" sentinel for the Monitor scope dropdown — surfaces the full
// universe scan (UST_FUT / DE_FUT / UK_FUT / JP_FUT / FR_FUT / IT_FUT /
// ES_FUT / CA_FUT / AU_FUT together) as ONE option.  Mirrors the
// universe-scope convention used by the ZCIS / linker scanner siblings.
const SCOPE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'ALL', label: 'All bond futures (UST / Bund / Gilt / JGB / ...)' },
  ...BOND_FUTURES_SCANNER_CURVE_OPTIONS,
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
  toolName: 'scan_bond_futures_extremes_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1).
  //     SCANNER shape: compact view is a top-N table, NOT a sparkline.
  //   * monitor_surface — universe-wide morning sweep is a desk-canonical
  //     glanceable read (surface_contract.md §3.4 eligibility — the
  //     desk's "where is the bond-futures universe stretched today?"
  //     check is a daily ritual).
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Bond Futures Universe Extremes',
  category: 'scanners',
  oneLineSummary:
    'Universe-wide front-month bond-futures sweep — ranks every rolling-generic stem (TY1 / UXY1 / US1 / WN1 / RX1 / UB1 / JB1 / G1 / OAT1 / ...) by absolute 252-day rolling z-score across FOUR metrics (price LEVEL, 1-day price CHANGE, volume LEVEL, open-interest LEVEL). Returns the top-N extremes per metric with the load-bearing V1-monitors-only / CTD-out-of-scope caveat per ADR 0013. Morning screen, not a basis or DV01-stack trade signal.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  Fetches the SAME /api/v1/rates/detail/bond-futures-scanner endpoint
  // the Build views use.  The catalog form exposes three knobs:
  //   * scope          — 'ALL' OR a single bond-futures curve family
  //   * top_n          — how many ranked extremes per metric to surface
  //   * min_abs_z_score — flagging threshold
  monitorWidgets: [
    {
      id: 'bond_futures_universe_extremes',
      label: 'Bond Futures Extremes',
      description:
        'Universe-wide bond-futures extremes (252d z-score across price · Δ · vol · OI). Surfaces the top-N flagged stems across the sovereign-bond futures universe — morning sweep tile.',
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
      component: BondFuturesScannerWidget,
    },
  ],

  // FM5 — defaults.  The scanner input is unique in that NO curve / tenor
  // is required — the default is "the full bond-futures universe".  Top-N
  // and threshold default to the YAML's bundled conventions (5 / 1.5);
  // ``__ALL__`` is the magic sentinel BuildExtended resolves to "omit
  // curve_families ⇒ scan full universe".
  defaultParams: {
    curve_families: '__ALL__',
    top_n: '5',
    min_abs_z_score: '1.5',
  },
};
