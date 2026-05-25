// ============================================================================
// src/modules/primitives/calculate_butterfly_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_butterfly_tool',
  tiers: ['workflow_incompatible'],
  displayName: 'calculate_butterfly',
  category: 'curve_shape',
  oneLineSummary: 'Three-point curvature on a sovereign curve — (2 × belly − short − long) × 100 bps — with rolling z-score, trailing range, wing-spread components, and full time series.',
  unsupportedReason: {
    label: 'calculate_butterfly',
    reason: 'Manifest-declared tool with a backend implementation behind a typed-detail endpoint (`/api/v1/rates/detail/<kind>`).  Not in `_PRIMITIVE_SPECS` so the workflow bridge cannot dispatch it; the typed view in Build is the live surface.',
    whatWorksNow: 'The typed view in Build renders this tool against its typed-detail endpoint.  Ask handoff works too — the tool is in `_MANIFEST_ONLY_BUILD_TOOLS` on the backend gate.',
  },
};
