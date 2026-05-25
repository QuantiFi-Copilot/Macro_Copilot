// ============================================================================
// src/modules/primitives/calculate_cross_market_spread_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Stage 4a — typed-view module.  Claims ``custom_build_surface`` because
// the typed view (formerly ``src/components/build/primitive/`` legacy
// location) now ships at ``surfaces/BuildSurface.tsx`` in this folder.
// Routes via ``MODULE.typedView = 'cross_market'`` through the context
// decoder's TOOL_TO_VIEW derivation.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildSurface from './surfaces/BuildSurface';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_cross_market_spread_tool',
  tiers: ['generic_runnable', 'custom_build_surface'],
  displayName: 'calculate_cross_market_spread',
  category: 'cross_market_rv',
  oneLineSummary: 'Yield differential between the same tenor on two sovereign curves (e.g. BTP-Bund 10Y), in basis points, with rolling 252-day z-score, daily / weekly / monthly change, trailing range, and full time series.',
  typedView: 'cross_market',
  surfaces: { build: BuildSurface },
};
