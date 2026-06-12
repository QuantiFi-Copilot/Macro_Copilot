// ============================================================================
// src/modules/primitives/get_futures_volume_oi_tool/module.ts — Dual-view build.
// ----------------------------------------------------------------------------
// Stage upgrade: Stage 3 scaffold (runtime tier only) → standalone-bridge
// plus a Monitor bento tile.
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/futures-volume-oi + own surfaces;
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// RUNTIME TIER — ``generic_runnable`` is KEPT (backend ships the tool in
// ``_PRIMITIVE_SPECS``; the workflow bridge dispatches it normally).  The
// capability tiers stack on top: the wire's TWO-series payload (volume +
// open interest, both whole-CONTRACT counts) plus the 252d OI stretch
// context is exactly the rich structure AutoRenderer can't honour, hence
// ``custom_build_surface``; the ΔOI / volume-vs-mean read is a desk
// morning glance, hence ``monitor_surface``.
//
// CONTRACT-COUNT honesty (ADR 0013 / P5) carried by every surface: volume
// and open_interest are counts, NEVER notional (multiply by contract_size
// for notional) and never bps; the wire ``methodology_disclosure`` renders
// verbatim on the extended methodology card.
//
// Sibling (policy_futures strip-slot analogue, P3):
// ../policy_futures_get_volume_open_interest_snapshot_tool/module.ts.
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No side
// effects, no global mutation, no register() calls.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { FuturesVolumeOiWidget } from './surfaces/monitor/FuturesVolumeOiWidget';
import {
  FUTURES_VOI_CONTRACT_OPTIONS,
  FUTURES_VOI_CURVE_OPTIONS,
} from './surfaces/futuresVolumeOiShared';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'get_futures_volume_oi_tool',

  // FM3 — tier claims:
  //   * generic_runnable — runtime-status tier KEPT verbatim from the
  //     Stage-3 scaffold (backend _PRIMITIVE_SPECS entry; bridge
  //     dispatches it normally).
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //     served by the standalone typed-detail bridge: the two-series
  //     contract-count payload + OI stretch context needs bespoke axes.
  //   * monitor_surface — ΔOI build/unwind + volume-vs-22d-mean IS a
  //     desk-glanceable morning read; surface_contract.md §3.4.
  tiers: ['generic_runnable', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata.
  displayName: 'Futures Volume OI',
  category: 'snapshots',
  oneLineSummary: 'Daily traded volume + end-of-day open interest for ONE rolling-generic bond-futures contract (TY1 / RX1 / G 1 / JB1 …) — current counts, 1-day OI change, 252d OI z-score / percentile / high-low range, and 22d rolling volume context.  Both series are whole-CONTRACT counts (NOT notional — multiply by FUT_CONT_SIZE for notional); pure-INGEST read per ADR 0013.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget.  Inherently compact
  // (rendering_density.md §8).  Parameterised on (curve_family,
  // contract_code, lookback_days); fetches the SAME
  // /api/v1/rates/detail/futures-volume-oi endpoint as both Build views.
  // Id uniqueness checked against src/components/monitor/registry.ts +
  // every monitorWidgets id (see tmp/wiring handoff): no volume/OI tile
  // existed for the bond-futures domain.
  monitorWidgets: [
    {
      id: 'bond_futures_volume_oi',
      label: 'Futures Volume / OI',
      description:
        'One rolling-generic bond-futures contract: end-of-day open interest (whole contracts, NOT notional), 1-day OI build/unwind, volume vs 22d mean, and the 252d OI range with a current marker.',
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
          options: FUTURES_VOI_CURVE_OPTIONS,
        },
        {
          kind: 'select',
          name: 'contract_code',
          label: 'Contract',
          defaultValue: 'TY1',
          options: FUTURES_VOI_CONTRACT_OPTIONS,
        },
      ],
      component: FuturesVolumeOiWidget,
    },
  ],

  // FM5 — defaults mirror the Pydantic Input's structural fields plus
  // the YAML lookback default (365).
  defaultParams: {
    curve_family: 'UST_FUT',
    contract_code: 'TY1',
    lookback_days: '365',
  },
};
