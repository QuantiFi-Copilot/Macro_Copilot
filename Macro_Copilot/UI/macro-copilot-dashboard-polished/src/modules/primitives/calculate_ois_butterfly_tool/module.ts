// ============================================================================
// src/modules/primitives/calculate_ois_butterfly_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_ois_butterfly_tool',
  tiers: ['generic_runnable'],
  displayName: 'calculate_ois_butterfly',
  category: 'spreads',
  oneLineSummary: 'Same-curve 3-point butterfly (curvature) on a single OIS par-swap.',
};
