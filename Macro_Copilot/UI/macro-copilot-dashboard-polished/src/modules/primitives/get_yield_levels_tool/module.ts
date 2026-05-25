// ============================================================================
// src/modules/primitives/get_yield_levels_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Stage 4a — typed-view module.  Claims ``custom_build_surface`` because
// the typed view (formerly ``src/components/build/primitive/`` legacy
// location) now ships at ``surfaces/BuildSurface.tsx`` in this folder.
// Routes via ``MODULE.typedView = 'yield'`` through the context
// decoder's TOOL_TO_VIEW derivation.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildSurface from './surfaces/BuildSurface';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'get_yield_levels_tool',
  tiers: ['generic_runnable', 'custom_build_surface'],
  displayName: 'get_yield_levels',
  category: 'snapshots',
  oneLineSummary: 'Single-tenor sovereign yield snapshot — current yield, daily / weekly / monthly change in bps, rolling 252-day z-score, trailing 252-day high / low / percentile, observation count, and full chartable time series.',
  typedView: 'yield',
  surfaces: { build: BuildSurface },
};
