// ============================================================================
// src/modules/primitives/get_scan_policy_futures_extremes_tool/module.ts
// ----------------------------------------------------------------------------
// Universe-wide policy-futures (STIR) extremes scanner under the dual-view +
// standalone-bridge contract.  SCANNER shape (multi-metric) — the wire
// returns a ranked LIST of (curve_family, strip_position, contract_code)
// extremes across FOUR metrics (implied-rate LEVEL, 1-day implied-rate
// CHANGE in bps, volume LEVEL, open-interest LEVEL), each independently
// ranked by absolute 252d-rolling z-score.  Compact view = top-N table
// (sorted cross-metric by |z|, with PACK + SCOPE chips per row);
// Extended view = universe-scan canvas (distribution histogram + multi-
// metric ranked detail table); Monitor tile = bento flagged-count + top-3
// cross-metric rows.
//
//   - methodology_exposure.md §5 standalone-bridge contract (own typed-
//     detail endpoint at /api/v1/rates/detail/policy-futures-scanner +
//   - rendering_density.md §1 dual-view mandate (BOTH buildExtended +
//     buildCompact REQUIRED; no opt-in)
//   - ADR 0013 V1 monitors-only scope (CTD analytics, term-premium
//     decomposition, meeting-by-meeting policy-path extraction are
//     Phase-4 work; surfaced on the methodology card + the per-tool
//     compact caveat)
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
import { PolicyFuturesScannerWidget } from './surfaces/monitor/PolicyFuturesScannerWidget';
import { POLICY_FUTURES_SCANNER_CURVE_OPTIONS } from './surfaces/policyFuturesScannerShared';

// "ALL" sentinel for the Monitor scope dropdown — surfaces the full
// universe scan (SOFR_FUT / EUR_SHORT_RATE_FUT / SONIA_FUT together) as
// ONE option.  Mirrors the universe-scope convention used by the sibling
// bond_futures / ZCIS / linker scanners.
const SCOPE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'ALL', label: 'All STIR (SOFR / Euribor / SONIA)' },
  ...POLICY_FUTURES_SCANNER_CURVE_OPTIONS,
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
  // FM1 — identity (folder name === toolName).  Note: the workflow
  // registry's canonical name is ``policy_futures_get_scan_policy_futures
  // _extremes_tool`` (sub-agent-prefixed); the frontend folder + MCP
  // function are both ``get_scan_policy_futures_extremes_tool``.  Mirrors
  // the existing ``policy_futures_get_futures_price_level_tool`` precedent
  // — no TS alias required because the workflow-registry name is not used
  // on the frontend surfaces.
  toolName: 'get_scan_policy_futures_extremes_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1).
  //     SCANNER shape: compact view is a top-N table, NOT a sparkline.
  //   * monitor_surface — universe-wide morning sweep is a desk-canonical
  //     glanceable read (surface_contract.md §3.4 eligibility — the
  //     desk's "where is the STIR universe stretched today?" check is a
  //     daily ritual ahead of Fed / ECB / BoE meeting clusters).
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'STIR Universe Extremes',
  category: 'scanners',
  oneLineSummary:
    'Universe-wide policy-futures strip sweep — ranks every (curve_family, strip_position) STIR stem (SFR1..8 / ER1..8 / SFI1..8) by absolute 252-day rolling z-score across FOUR metrics (implied-rate LEVEL, 1-day implied-rate CHANGE in bps, volume LEVEL, open-interest LEVEL). Returns the top-N extremes per metric with the load-bearing ADR 0013 V1 monitors-only / RFR-vs-IBOR regime caveat per row. Morning screen ahead of Fed / ECB / BoE meeting clusters — not a meeting-by-meeting policy-path decomposition.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact (rendering_density.md
  // §8).  Fetches the SAME /api/v1/rates/detail/policy-futures-scanner
  // endpoint the Build views use.  The catalog form exposes three knobs:
  //   * scope          — 'ALL' OR a single STIR curve family
  //   * top_n          — how many ranked extremes per metric to surface
  //   * min_abs_z_score — flagging threshold
  monitorWidgets: [
    {
      id: 'policy_futures_universe_extremes',
      label: 'STIR Extremes',
      description:
        'Universe-wide policy-futures (STIR) extremes (252d z-score across IR · Δ · vol · OI). Surfaces the top-N flagged stems across SOFR / Euribor / SONIA — morning sweep tile ahead of Fed / ECB / BoE meeting clusters.',
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
      component: PolicyFuturesScannerWidget,
    },
  ],

  // FM5 — defaults.  The scanner input is unique in that NO curve / strip
  // is required — the default is "the full policy-futures universe".
  // Top-N and threshold default to the YAML's bundled conventions (5 /
  // 1.5); ``__ALL__`` is the magic sentinel BuildExtended resolves to
  // "omit curve_families ⇒ scan full universe".
  defaultParams: {
    curve_families: '__ALL__',
    top_n: '5',
    min_abs_z_score: '1.5',
  },
};
