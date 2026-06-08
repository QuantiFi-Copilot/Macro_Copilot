// ============================================================================
// src/modules/primitives/get_futures_price_level_tool/module.ts
// ----------------------------------------------------------------------------
// Dispatch 1 — initial dual-view build per the catalog's tool-23 entry
// (build_order 23; bond_futures domain).  Under the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/bond-futures-price + own
//     surfaces; no shared typedView)
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// Naming note (FM1): the folder + tool.name use the BARE
// ``get_futures_price_level_tool`` — not a domain-prefixed alias.
// The policy_futures cousin is at
// ``policy_futures_get_futures_price_level_tool/`` and routes via
// ``KNOWN_TOOL_ALIASES`` for un-prefixed MCP-name traffic.  Bond_futures
// holds the bare name because it was registered first under that name
// in the workflow registry + tool_metadata DB row.
//
// Reference twin: policy_futures_get_futures_price_level_tool (Batch 1
// d8e8233) — same snapshot shape (level + period changes + 252d range +
// z-score + bespoke time_series), different curve family + unit space.
// Mockup-faithful design captured in surfaces/BuildExtended.tsx +
// surfaces/BuildCompact.tsx; compact view uses the SHELL-STANDARD density
// per the Option-(c) precedent.  See THESIS.md → Mockup conformance.
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import { LOOKBACK_OPTIONS } from '@/lib/monitorParamOptions';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { BondFuturesPriceLevelWidget } from './surfaces/monitor/BondFuturesPriceLevelWidget';
import {
  BOND_FUTURES_CONTRACT_OPTIONS,
  BOND_FUTURES_CURVE_OPTIONS,
} from './surfaces/bondFuturesPriceShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'get_futures_price_level_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //   * monitor_surface — bond-futures rolling-generic price is a desk-
  //     canonical morning read for sovereign rates; surfacing as a
  //     glanceable tile fits the surface_contract.md §3.4 eligibility.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Bond Futures Price Level',
  category: 'snapshots',
  oneLineSummary:
    'Single rolling-generic bond-futures price for one (curve_family, contract_code) pair (e.g. TY1 on UST_FUT, RX1 on DE_FUT, G1 on UK_FUT). Carries 1D / 5D / 1M raw-price changes in the contract\'s native quote_units (points / % of par value / 100 - yield / GBP), 252d high/low/percentile, rolling 252d z-score, and the per-contract SCD2 disclosure (security_name, expiry_date, contract_size).',

  // FM9 — STANDALONE pattern per methodology_exposure.md §5: no shared
  // typedView.  The module owns its own full Build surfaces.
  typedView: null,
  richModel: false,

  // FM8 — dual Build-side surfaces (rendering_density.md §5).  ``build``
  // is kept === buildExtended for the legacy VirtualPrimitiveCanvas
  // dispatcher that reads ``surfaces.build``.
  surfaces: {
    build: BuildExtended,
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Per rendering_density.md §8 inherently
  // compact — renders at small/medium widget sizes in the bento grid.
  // Parameterised on (curve_family, contract_code, lookback_days); fetches
  // the SAME typed-detail endpoint the Build views use per the standalone-
  // bridge contract.  Widget ID is globally unique (NOT
  // 'policy_futures_price_level' which the cousin holds) — see catalog-23
  // collision watch.
  monitorWidgets: [
    {
      id: 'bond_futures_price_level',
      label: 'Bond Futures Price Level',
      description:
        'Rolling-generic bond-futures price for one (curve_family, contract_code) pair (TY1 / UXY1 / RX1 / G1 / JB1 / OAT1 / IK1 / KOA1 / CN1 / YM1 / XM1 / ...) with 1d change, 252d z-score, and range strip.',
      category: 'data',
      defaultSize: 'small',
      allowedSizes: ['small', 'medium'],
      parameterized: true,
      paramFields: [
        {
          kind: 'select',
          name: 'curve_family',
          label: 'Curve family',
          defaultValue: 'UST_FUT',
          options: BOND_FUTURES_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'contract_code',
          label: 'Contract',
          defaultValue: 'TY1',
          options: BOND_FUTURES_CONTRACT_OPTIONS,
        },
        {
          kind: 'select',
          name: 'lookback_days',
          label: 'Lookback',
          defaultValue: '252',
          options: LOOKBACK_OPTIONS,
        },
      ],
      component: BondFuturesPriceLevelWidget,
    },
  ],

  // FM5 — defaults mirror the structural Pydantic Input fields the backend
  // requires (curve_family, contract_code per TD#11) plus the YAML-locked
  // ``default_price_field`` convention sentinel.
  defaultParams: {
    curve_family: 'UST_FUT',
    contract_code: 'TY1',
    lookback_days: '365',
    field_name: 'PX_LAST',
  },
};
