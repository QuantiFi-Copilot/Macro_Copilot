// ============================================================================
// src/modules/primitives/build_sovereign_yield_panel_tool/module.ts
// ----------------------------------------------------------------------------
// PANEL-BUILDER dual-view module under the new standards:
//   - methodology_exposure.md §5 standalone bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/sovereign-yield-panel + own surfaces; no shared
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// PANEL shape: the wire carries the panel's METADATA CONTRACT (dims /
// '<curve_family>_<tenor>' columns / date range / units / disclosures) —
// the assembled cell matrix is a workflow-side Panel artifact the MCP layer
// and the detail route both drop.  Both surfaces are CONTRACT cards (no
// chart — no series on the wire; THESIS Q3).
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.  The
// default date window is computed at RENDER time in
// surfaces/sovereignYieldPanelShared.ts::defaultWindow().
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'build_sovereign_yield_panel_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  // NO monitor_surface: a panel contract is assembly metadata, not
  // glanceable morning state (THESIS Q3 documents the non-claim).
  tiers: ['generic_runnable', 'custom_build_surface'],

  // FM5 — display metadata
  displayName: 'Build Sovereign Yield Panel',
  category: 'panel_assembly',
  oneLineSummary: 'Wide multi-instrument Panel of sovereign yields keyed by `<curve_family>_<tenor>` over a date range. Designed as the backtest workflow\'s primary price-source panel — every cash leg in a sovereign-family trade reads its time series from one row of the assembled Panel.',


  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },
};
