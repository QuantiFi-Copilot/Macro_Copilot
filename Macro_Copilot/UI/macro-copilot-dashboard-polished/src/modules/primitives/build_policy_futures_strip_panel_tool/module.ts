// ============================================================================
// src/modules/primitives/build_policy_futures_strip_panel_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'build_policy_futures_strip_panel_tool',
  tiers: ['generic_runnable'],
  displayName: 'Build Policy Futures Strip Panel',
  category: 'panels',
  oneLineSummary: 'Assemble a wide multi-instrument Panel of policy-futures.',
};
