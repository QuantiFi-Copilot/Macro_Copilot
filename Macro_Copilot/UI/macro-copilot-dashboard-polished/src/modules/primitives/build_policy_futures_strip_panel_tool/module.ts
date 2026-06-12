// ============================================================================
// src/modules/primitives/build_policy_futures_strip_panel_tool/module.ts
// ----------------------------------------------------------------------------
// PANEL-BUILDER dual-view module under the new standards:
//   - methodology_exposure.md §5 standalone bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/policy-futures-strip-panel + own surfaces; no
//     shared typedView)
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// PANEL shape: the wire carries the panel's METADATA CONTRACT (dims /
// '<CURVE_FAMILY>|<STRIP_POSITION>' column keys / date range / units /
// methodology card) — the assembled implied-rate cell matrix is a
// workflow-side Panel artifact the MCP layer and the detail route both
// drop.  Both surfaces are CONTRACT cards (no chart — no series on the
// wire; THESIS Q3).
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.  The
// default date window is computed at RENDER time in
// surfaces/policyFuturesStripPanelShared.ts::defaultWindow() — the required
// ``start_date`` never defaults at module-eval (sovereign-sibling approach).
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'build_policy_futures_strip_panel_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  // NO monitor_surface: a panel contract is assembly metadata, not
  // glanceable morning state (THESIS Q3 documents the non-claim).
  tiers: ['generic_runnable', 'custom_build_surface'],

  // FM5 — display metadata (displayName / category preserved from the
  // Stage-3 scaffold)
  displayName: 'Build Policy Futures Strip Panel',
  category: 'panels',
  oneLineSummary: 'Wide multi-instrument Panel of policy-futures IMPLIED RATES (percent) keyed by `<CURVE_FAMILY>|<STRIP_POSITION>` over a date range, across the SOFR/SONIA/Euribor strips. Designed as the STIR backtest workflow\'s substrate — cross-central-bank comparison, strip-curve PCA and RV scans all read their per-slot implied-rate series from one assembled Panel.',

  // FM9 — STANDALONE pattern (methodology_exposure.md §5): no shared typedView.
  typedView: null,
  richModel: false,

  // FM8 — dual Build-side surfaces (rendering_density.md §5).  ``build`` is
  // kept === buildExtended for the legacy VirtualPrimitiveCanvas dispatcher
  // (and FM8 invariant 4, which maps custom_build_surface → surfaces.build).
  surfaces: {
    build: BuildExtended,
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },
};
