// ============================================================================
// src/modules/primitives/policy_futures_get_volume_open_interest_snapshot_tool/module.ts
// — Dual-view build.
// ----------------------------------------------------------------------------
// Stage upgrade: Stage 3 scaffold (runtime tier only) → standalone-bridge
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/policy-futures-voi-snapshot + own
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// RUNTIME TIER — ``generic_runnable`` is KEPT (backend ships the tool in
// ``_PRIMITIVE_SPECS``; the workflow bridge dispatches it normally).  The
// ``custom_build_surface`` tier stacks on top: the wire's TWO-series
// payload (volume + open interest, whole-CONTRACT counts) plus the 252d
// OI stretch context is exactly the rich structure AutoRenderer can't
// honour.  No monitor tier — the strip-snapshot sibling's panel is the
// desk's policy-futures morning read; a per-slot OI tile would duplicate
// it at lower information density (THESIS Q3).
//
// Sibling (bond_futures rolling-generic analogue — P3, mirrored
// cell-for-cell; same Pydantic Output class):
// ../get_futures_volume_oi_tool/module.ts.
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No side
// effects, no global mutation, no register() calls.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'policy_futures_get_volume_open_interest_snapshot_tool',

  // FM3 — tier claims:
  //   * generic_runnable — runtime-status tier KEPT verbatim from the
  //     Stage-3 scaffold (backend _PRIMITIVE_SPECS entry).
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  //     served by the standalone typed-detail bridge.
  tiers: ['generic_runnable', 'custom_build_surface'],

  // FM5 — display metadata.
  displayName: 'Policy Futures Volume Open Interest Snapshot',
  category: 'snapshots',
  oneLineSummary: 'Daily traded volume + end-of-day open interest for ONE STIR strip slot keyed by (curve_family, strip_position) — SOFR / Euribor / SONIA, positions 1-8 (whites + reds) — current counts, 1-day OI change, 252d OI z-score / percentile / high-low range, and 22d rolling volume context.  Both series are whole-CONTRACT counts (NOT notional — multiply by FUT_CONT_SIZE for notional); pure-INGEST read per ADR 0013.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5 — defaults mirror the Pydantic Input's structural fields plus
  // the YAML lookback default (365).
  defaultParams: {
    curve_family: 'SOFR_FUT',
    strip_position: '1',
    lookback_days: '365',
  },
};
