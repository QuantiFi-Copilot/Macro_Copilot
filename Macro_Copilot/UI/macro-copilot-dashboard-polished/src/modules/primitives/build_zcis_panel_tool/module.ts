// ============================================================================
// src/modules/primitives/build_zcis_panel_tool/module.ts
// ----------------------------------------------------------------------------
// PANEL-BUILDER dual-view module under the new standards:
//   - methodology_exposure.md §5 standalone bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/zcis-panel + own surfaces; no shared typedView)
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// PANEL shape: the wire carries the panel's METADATA CONTRACT (dims /
// vendor_ticker columns / date range / units / methodology_card with the
// security_name + index-family caveats and the per-family index_lag /
// interpolation reference) — the assembled cell matrix is a workflow-side
// Panel artifact the MCP layer and the detail route both drop.  Both
// surfaces are CONTRACT cards (no chart — no series on the wire; THESIS Q3).
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.  The
// default date window is computed at RENDER time in
// surfaces/zcisPanelShared.ts::defaultWindow().
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'build_zcis_panel_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  // NO monitor_surface: a panel contract is assembly metadata, not
  // glanceable morning state (THESIS Q3 documents the non-claim).
  tiers: ['generic_runnable', 'custom_build_surface'],

  // FM5 — display metadata
  displayName: 'Build ZCIS Panel',
  category: 'panels',
  oneLineSummary: 'Wide multi-instrument Panel of zero-coupon inflation swap rates keyed by vendor_ticker across the USD_ZCIS / EUR_ZCIS / GBP_ZCIS universe. Designed as the inflation desk\'s cross-curve substrate — regression / PCA / RV-scan operators read each swap\'s time series from one column of the assembled Panel.',

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
