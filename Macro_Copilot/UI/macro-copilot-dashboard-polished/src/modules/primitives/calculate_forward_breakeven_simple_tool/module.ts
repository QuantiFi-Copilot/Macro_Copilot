// ============================================================================
// src/modules/primitives/calculate_forward_breakeven_simple_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view + monitor module under the new standards:
//   - methodology_exposure.md §5 standalone-bridge contract (this tool
//     ships its own typed-detail endpoint at
//     /api/v1/rates/detail/forward-breakeven + its own frontend surfaces;
//     no shared typedView reuse)
//   - rendering_density.md §1 dual-view mandate (BOTH buildExtended +
//     buildCompact REQUIRED; no opt-in)
//
// Design reference: mockups/Compact.png + mockups/Extended.png in this
// folder, committed alongside the module per the project's mockup-first
// workflow.
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No
// side effects, no global mutation, no register() calls.
//
// FM1 identity note: the folder name `calculate_forward_breakeven_simple_tool`
// matches the MCP function + workflow registry name exactly — no alias
// bridge needed (mirrors the sibling calculate_breakeven_inflation_simple_tool /
// calculate_breakeven_curve_spread_tool / calculate_breakeven_butterfly_tool
// — same inflation_indexed_bonds sub-domain).
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import {
  FORWARD_BREAKEVEN_PAIR_OPTIONS,
  FORWARD_PAIR_OPTIONS,
} from './surfaces/forwardBreakevenShared';
import { ForwardBreakevenWidget } from './surfaces/monitor/ForwardBreakevenWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_forward_breakeven_simple_tool',

  // FM3 — tier claims (parity with calculate_breakeven_inflation_simple_tool):
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — forward breakevens are the canonical term-structure
  //     read on long-run inflation compensation; 5Y5Y US BE is a desk-glanceable
  //     morning-briefing read per surface_contract.md §3.4 eligibility
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Forward Breakeven Inflation',
  category: 'forward_rate',
  oneLineSummary:
    'Forward bond-implied breakeven inflation between two same-country pillars (e.g. UST/USD_TIPS 5Y5Y, FR_OAT/EUR_FR_LINKER 5Y10Y) computed as the year-weighted linear forward of two spot bond-implied breakevens.  Carries forward_breakeven_bps + 1d/5d/1m changes + rolling 252d z-score + trailing 252d high/low/percentile + start/end spot breakeven legs for decomposition audit.  Forward inflation compensation — NOT a clean forward expected-inflation read.',

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
  // §8).  ``pair`` is the linker curve_family that uniquely picks the same-
  // country nominal counterparty; the widget expands it to both legs.
  // ``forward_pair`` encodes (start_tenor, end_tenor).  Fetches the SAME
  // /api/v1/rates/detail/forward-breakeven endpoint per the standalone-bridge
  // contract in methodology_exposure.md §5.4.
  monitorWidgets: [
    {
      id: 'forward_breakeven_simple',
      label: 'Forward Breakeven',
      description:
        'Forward bond-implied breakeven inflation for one same-country pair (e.g. US 5Y5Y).  Forward inflation compensation, not expected inflation.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'pair',
          label: 'Country pair',
          defaultValue: 'USD_TIPS',
          options: FORWARD_BREAKEVEN_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'forward_pair',
          label: 'Forward',
          defaultValue: '5Y5Y',
          options: FORWARD_PAIR_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: ForwardBreakevenWidget,
    },
  ],

  // FM5 — defaults.  Mockup default: UST/USD_TIPS 5Y5Y (US 5Y5Y BE — the
  // desk-canonical long-run inflation-compensation anchor).  start_tenor /
  // end_tenor are pre-set so the backend's tenor-mode input accepts the
  // params dict directly (the Build surfaces re-derive both from
  // forward_pair on change, keeping them in sync).
  defaultParams: {
    nominal_curve_family: 'UST',
    linker_curve_family: 'USD_TIPS',
    forward_pair: '5Y5Y',
    start_tenor: '5Y',
    end_tenor: '10Y',
    lookback_days: '365',
    field_name: 'YLD_YTM_MID',
  },
};
