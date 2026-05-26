// ============================================================================
// src/modules/primitives/policy_futures_get_volume_open_interest_snapshot_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'policy_futures_get_volume_open_interest_snapshot_tool',
  tiers: ['generic_runnable'],
  displayName: 'Policy Futures Volume Open Interest Snapshot',
  category: 'snapshots',
  oneLineSummary: 'Single policy-futures strip-position volume + open-interest.',
};
