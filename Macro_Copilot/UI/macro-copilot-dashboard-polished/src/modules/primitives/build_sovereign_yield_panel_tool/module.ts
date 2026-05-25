// ============================================================================
// src/modules/primitives/build_sovereign_yield_panel_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'build_sovereign_yield_panel_tool',
  tiers: ['generic_runnable'],
  displayName: 'build_sovereign_yield_panel',
  category: 'panel_assembly',
  oneLineSummary: 'Wide multi-instrument Panel of sovereign yields keyed by `<curve_family>_<tenor>` over a date range. Designed as the backtest workflow\'s primary price-source panel — every cash leg in a sovereign-family trade reads its time series from one row of the assembled Panel.',
};
