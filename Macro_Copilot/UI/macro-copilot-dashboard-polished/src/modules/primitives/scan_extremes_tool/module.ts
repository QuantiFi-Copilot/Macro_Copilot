// ============================================================================
// src/modules/primitives/scan_extremes_tool/module.ts — Migration-mode rewrite.
// ----------------------------------------------------------------------------
// surfaces.{build, buildExtended, buildCompact}).
//
// PRESERVED VERBATIM per MIGRATION_RULES.md §6 (backward-compat lock):
//   * ``manifest_typed_view`` tier (backend ``_PRIMITIVE_SPECS`` does NOT
//     contain this tool — mirror backend reality; do NOT swap to
//     ``generic_runnable``).
//   * Monitor widget id ``'scanner'`` + paramFields shape unchanged so
//     dashboards mounting it continue to hydrate.  The widget's data source
//     (the pre-aggregated ``RatesDataContext`` feed) is preserved so legacy
//     RatesPage consumers stay byte-identical.
//
// SCANNER shape (rendering_density.md §2.2 / Option (c) precedent): the wire
// returns a ranked LIST of (curve_family, tenor) extremes ordered by |z| of
// the 252d-rolling YLD_YTM_MID z-score, NOT a single time series — so this
// module's compact view is a top-N TABLE and its extended view is a
// universe-scan canvas (distribution histogram + ranked detail table).
//
//   - methodology_exposure.md §5 standalone-bridge contract (own typed-detail
//     endpoint at /api/v1/rates/detail/scanner + own surfaces; no shared
//   - rendering_density.md §1 dual-view mandate (BOTH buildExtended +
//     buildCompact REQUIRED)
//
// Design reference: mockups/Compact.png + mockups/Extended.png in this
// folder.  Sibling: ../scan_inflation_swaps_extremes_tool/module.ts.
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No side
// effects, no global mutation, no register() calls.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { ScannerWidget } from './surfaces/monitor/ScannerWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'scan_extremes_tool',

  // FM3 — tier claims.  ``manifest_typed_view`` is PRESERVED from the
  // legacy module.ts: backend ``WORKFLOW_INCOMPATIBLE_TOOLS`` /
  // ``_PRIMITIVE_SPECS`` membership is unchanged; the tier set mirrors
  // backend reality (MIGRATION_RULES.md §4 step 6 / §8 anti-pattern).
  tiers: ['manifest_typed_view', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata.
  displayName: 'Scan Extremes',
  category: 'screening',
  oneLineSummary:
    'Scans every sovereign benchmark instrument in the database, ranks the top-N by absolute 252-day z-score and returns yield, daily change, z-score, percentile, and signal label per row.',

  // the per-tool typed-detail endpoint ``/api/v1/rates/detail/scanner``.

  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  surfaces: {
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widgets.  PRESERVED VERBATIM per the migration's
  // backward-compat lock: id, label, paramFields, component all unchanged.
  // The ``scanner`` widget is non-parameterised (the legacy hardcoded
  // threshold + cap remain) and continues to read from the global
  // ``RatesDataContext`` feed; refactor to per-widget fetch is deferred so
  // that every dashboard mounting this widget renders identically pre/post
  // migration.
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

  // FM6 — unsupportedReason is REQUIRED by the framework invariant when
  // the runtime-status tier is ``manifest_typed_view`` (see
  // ``src/modules/__test-utils.ts`` invariant #8).  The catalog entry's
  // legacy_migration.module_ts_changes.remove_fields list includes
  // framework contract OVERRIDES the catalog here: keeping
  // ``manifest_typed_view`` (which we MUST per MIGRATION_RULES.md §4 step
  // 6) means keeping a populated ``unsupportedReason`` block.  The COPY is
  // updated to describe the new dual-view affordance — the typed view is
  // replaced by the per-tool ``BuildExtended`` / ``BuildCompact`` pair,
  // but the backend dispatch reality (``_MANIFEST_ONLY_BUILD_TOOLS`` set
  // membership) is unchanged.
  unsupportedReason: {
    label: 'scan_extremes',
    reason:
      'Manifest-declared tool with a backend implementation behind a typed-detail endpoint (`/api/v1/rates/detail/scanner`).  Not in `_PRIMITIVE_SPECS` so the workflow bridge cannot dispatch it; the dual-view Build surfaces in this module are the live read.',
    whatWorksNow:
      'The per-tool BuildExtended (universe-scan canvas) + BuildCompact (top-N table) surfaces in Build render this tool against its typed-detail endpoint.  Ask handoff works too — the tool is in `_MANIFEST_ONLY_BUILD_TOOLS` on the backend gate.',
  },
};
