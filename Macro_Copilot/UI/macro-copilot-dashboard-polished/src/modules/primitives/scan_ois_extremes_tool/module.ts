// ============================================================================
// src/modules/primitives/scan_ois_extremes_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'scan_ois_extremes_tool',
  tiers: ['paused'],
  displayName: 'scan_ois_extremes',
  category: 'screening',
  oneLineSummary: 'Scans every OIS instrument in the database, ranks the top-N by absolute 252-day z-score and returns rate, daily change, z-score, percentile, and signal label per row.  OIS analogue of scan_extremes.',
  unsupportedReason: {
    label: 'scan_ois_extremes',
    reason: 'Declared in the manifest but the backend has no live route yet.',
    whatWorksNow: 'Ask cannot run it today; the sovereign-bond scanner is the closest equivalent.',
  },
};
