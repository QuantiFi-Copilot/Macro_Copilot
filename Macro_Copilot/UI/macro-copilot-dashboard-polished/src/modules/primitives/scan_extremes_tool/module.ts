// ============================================================================
// src/modules/primitives/scan_extremes_tool/module.ts — Stage 4d.
// ----------------------------------------------------------------------------
// Stage 4a — typed-view module: ``custom_build_surface`` ships at
// ``surfaces/BuildSurface.tsx``; routes via ``typedView = 'scanner'``.
// Stage 4d — Monitor catalog: the global z-score scanner widget.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildSurface from './surfaces/BuildSurface';
import { ScannerWidget } from './surfaces/monitor/ScannerWidget';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'scan_extremes_tool',
  // Stage 4e: ``manifest_typed_view`` (not ``workflow_incompatible``)
  // — backend's ``WORKFLOW_INCOMPATIBLE_TOOLS`` dict does NOT contain
  // this tool; it's a ``_MANIFEST_ONLY_BUILD_TOOLS`` entry.
  tiers: ['manifest_typed_view', 'custom_build_surface', 'monitor_surface'],
  displayName: 'Scan Extremes',
  category: 'screening',
  oneLineSummary:
    'Scans every sovereign instrument in the database, ranks the top-N by absolute 252-day z-score and returns yield, daily change, z-score, percentile, and signal label per row.',
  typedView: 'scanner',
  workspaceLabel: 'Scanner results & heatmap',
  surfaces: { build: BuildSurface },
  monitorWidgets: [
    {
      id: 'scanner',
      label: 'Z-Score Scanner',
      description:
        'Top instruments flagged above your z-score threshold across the global universe.',
      category: 'anomaly',
      defaultSize: 'medium',
      allowedSizes: ['medium'],
      parameterized: false,
      component: ScannerWidget,
    },
  ],
  unsupportedReason: {
    label: 'scan_extremes',
    reason: 'Manifest-declared tool with a backend implementation behind a typed-detail endpoint (`/api/v1/rates/detail/<kind>`).  Not in `_PRIMITIVE_SPECS` so the workflow bridge cannot dispatch it; the typed view in Build is the live surface.',
    whatWorksNow: 'The typed view in Build renders this tool against its typed-detail endpoint.  Ask handoff works too — the tool is in `_MANIFEST_ONLY_BUILD_TOOLS` on the backend gate.',
  },
};
