// ============================================================================
// src/modules/primitives/scan_extremes_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Stage 4a — typed-view module.  Claims ``custom_build_surface`` because
// the typed view (formerly ``src/components/build/primitive/`` legacy
// location) now ships at ``surfaces/BuildSurface.tsx`` in this folder.
// Routes via ``MODULE.typedView = 'scanner'`` through the context
// decoder's TOOL_TO_VIEW derivation.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildSurface from './surfaces/BuildSurface';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'scan_extremes_tool',
  tiers: ['workflow_incompatible', 'custom_build_surface'],
  displayName: 'scan_extremes',
  category: 'screening',
  oneLineSummary: 'Scans every sovereign instrument in the database, ranks the top-N by absolute 252-day z-score and returns yield, daily change, z-score, percentile, and signal label per row.',
  typedView: 'scanner',
  surfaces: { build: BuildSurface },
  unsupportedReason: {
    label: 'scan_extremes',
    reason: 'Manifest-declared tool with a backend implementation behind a typed-detail endpoint (`/api/v1/rates/detail/<kind>`).  Not in `_PRIMITIVE_SPECS` so the workflow bridge cannot dispatch it; the typed view in Build is the live surface.',
    whatWorksNow: 'The typed view in Build renders this tool against its typed-detail endpoint.  Ask handoff works too — the tool is in `_MANIFEST_ONLY_BUILD_TOOLS` on the backend gate.',
  },
};
