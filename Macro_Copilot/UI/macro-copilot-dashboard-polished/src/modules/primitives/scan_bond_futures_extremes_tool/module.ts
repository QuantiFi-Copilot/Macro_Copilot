// ============================================================================
// src/modules/primitives/scan_bond_futures_extremes_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'scan_bond_futures_extremes_tool',
  tiers: ['generic_runnable'],
  displayName: 'scan_bond_futures_extremes',
  category: 'scanners',
  oneLineSummary: 'Universe-wide front-month bond-futures sweep — ranks every.',
};
