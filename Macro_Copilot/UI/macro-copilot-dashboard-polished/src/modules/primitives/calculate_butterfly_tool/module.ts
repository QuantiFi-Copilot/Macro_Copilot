// ============================================================================
// src/modules/primitives/calculate_butterfly_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Stage 4a — typed-view module.  Claims ``custom_build_surface`` because
// the typed view (formerly ``src/components/build/primitive/`` legacy
// location) now ships at ``surfaces/BuildSurface.tsx`` in this folder.
// Routes via ``MODULE.typedView = 'butterfly'`` through the context
// decoder's TOOL_TO_VIEW derivation.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import ResultRenderer from './surfaces/ResultRenderer';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'calculate_butterfly_tool',
  // Stage 4e: ``manifest_typed_view`` (not ``workflow_incompatible``)
  // because backend's ``WORKFLOW_INCOMPATIBLE_TOOLS`` dict does NOT
  // contain this tool — it's a ``_MANIFEST_ONLY_BUILD_TOOLS`` entry
  // with a typed-detail endpoint.  Pre-Stage-4e modules mis-classified
  // this as ``workflow_incompatible``; Stage 4e introduces the dedicated
  // tier so the spec stays aligned with backend doctrine.
  tiers: ['manifest_typed_view', 'custom_build_surface'],
  displayName: 'Butterfly',
  category: 'curve_shape',
  oneLineSummary: 'Three-point curvature on a sovereign curve — (2 × belly − short − long) × 100 bps — with rolling z-score, trailing range, wing-spread components, and full time series.',
  typedView: 'butterfly',
  workspaceLabel: 'Butterfly decomposition',
  surfaces: { resultRenderer: ResultRenderer },
  unsupportedReason: {
    label: 'calculate_butterfly',
    reason: 'Manifest-declared tool with a backend implementation behind a typed-detail endpoint (`/api/v1/rates/detail/<kind>`).  Not in `_PRIMITIVE_SPECS` so the workflow bridge cannot dispatch it; the typed view in Build is the live surface.',
    whatWorksNow: 'The typed view in Build renders this tool against its typed-detail endpoint.  Ask handoff works too — the tool is in `_MANIFEST_ONLY_BUILD_TOOLS` on the backend gate.',
  },
};
