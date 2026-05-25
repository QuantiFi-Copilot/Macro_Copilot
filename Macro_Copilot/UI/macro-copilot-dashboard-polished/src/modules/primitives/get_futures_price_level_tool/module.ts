// ============================================================================
// src/modules/primitives/get_futures_price_level_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'get_futures_price_level_tool',
  tiers: ['generic_runnable'],
  displayName: 'get_futures_price_level',
  category: 'snapshots',
  oneLineSummary: 'Single rolling-generic bond-futures price level — emits a.',
};
