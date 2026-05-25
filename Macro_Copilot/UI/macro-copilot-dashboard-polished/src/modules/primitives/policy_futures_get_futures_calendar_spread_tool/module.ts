// ============================================================================
// src/modules/primitives/policy_futures_get_futures_calendar_spread_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'policy_futures_get_futures_calendar_spread_tool',
  tiers: ['generic_runnable'],
  displayName: 'policy_futures_get_futures_calendar_spread',
  category: 'spreads',
  oneLineSummary: 'Same-curve calendar spread on the policy-futures strip — emits a.',
};
