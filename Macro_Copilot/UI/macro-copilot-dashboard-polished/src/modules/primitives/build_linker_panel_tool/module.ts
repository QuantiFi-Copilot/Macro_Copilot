// ============================================================================
// src/modules/primitives/build_linker_panel_tool/module.ts
// ----------------------------------------------------------------------------
// PANEL-BUILDER dual-view module under the new standards:
//   - methodology_exposure.md §5 standalone bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/linker-panel + own surfaces; no shared
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// PANEL shape: the wire carries the panel's METADATA CONTRACT (dims /
// vendor_ticker columns / date range / units / methodology_card caveats) —
// the assembled cell matrix is a workflow-side Panel artifact the MCP layer
// and the detail route both drop.  Both surfaces are CONTRACT cards (no
// chart — no series on the wire; THESIS Q3).  NO tenors knob — linkers are
// specific-maturity bonds, not tenor-pillar swaps.
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.  The
// default date window is computed at RENDER time in
// surfaces/linkerPanelShared.ts::defaultWindow().
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'build_linker_panel_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  // NO monitor_surface: a panel contract is assembly metadata, not
  // glanceable morning state (THESIS Q3 documents the non-claim).
  tiers: ['generic_runnable', 'custom_build_surface'],

  // FM5 — display metadata
  displayName: 'Build Linker Panel',
  category: 'panels',
  oneLineSummary: 'Wide multi-instrument Panel of inflation-linker REAL yields keyed by vendor_ticker across the USD_TIPS / GBP_LINKER / EUR_FR_LINKER / CAD_RRB universe over a date range. Substrate for cross-country real-yield RV scanning, PCA and operator workflows — every linker leg reads its time series from one column of the assembled Panel.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },
};
