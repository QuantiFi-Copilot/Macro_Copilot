// ============================================================================
// src/modules/primitives/calculate_curve_spread_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Stage 4a — typed-view module.  Claims ``custom_build_surface`` because
// the typed view (formerly ``src/components/build/primitive/`` legacy
// location) now ships at ``surfaces/BuildSurface.tsx`` in this folder.
// Routes via ``MODULE.typedView = 'spread'`` through the context
// decoder's TOOL_TO_VIEW derivation.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildSurface from './surfaces/BuildSurface';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_curve_spread_tool',
  tiers: ['generic_runnable', 'custom_build_surface'],
  displayName: 'calculate_curve_spread',
  category: 'curve_shape',
  oneLineSummary: 'Basis-point spread between two tenors on the same sovereign yield curve, with a fixed 1-year rolling z-score and full chartable time series.',
  typedView: 'spread',
  surfaces: { build: BuildSurface },
};
