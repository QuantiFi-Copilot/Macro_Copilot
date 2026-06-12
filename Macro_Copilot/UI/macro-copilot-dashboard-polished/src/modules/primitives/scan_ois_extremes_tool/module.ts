// ============================================================================
// src/modules/primitives/scan_ois_extremes_tool/module.ts — Dual-view build.
// ----------------------------------------------------------------------------
// Stage upgrade: Stage 3 scaffold (tiers ['paused'] — "no live route yet")
// → standalone-bridge dual-view (typedView=null +
// surfaces.{build, buildExtended, buildCompact}).  The backend route IS live
// now: /api/v1/rates/detail/ois-scanner (api/routes/rates/detail.py) wraps
// rates_agent/ois/tools/scan_ois_extremes — so the stale 'paused' claim is
// replaced.
//
// Tier shape MIRRORS the sovereign twin ``../scan_extremes_tool/module.ts``
// (P3 — the two LEVEL-metric scanner modules cannot drift):
//   * ``manifest_typed_view`` runtime tier — the tool ships in the
//     orchestrator's ``_MANIFEST_ONLY_BUILD_TOOLS`` set
//     (orchestrator/events.py), exactly like the sovereign twin; the tier
//     set mirrors backend reality (MIGRATION_RULES.md §4 step 6 / §8
//     anti-pattern — do NOT swap to ``generic_runnable``).
//   * ``custom_build_surface`` — dual-view Build pair.
//   No monitor tier: unlike the sovereign twin there is no legacy
//   dashboard widget to preserve (the twin's tile is a backward-compat
//   lock on a pre-aggregated RatesPage feed that has no OIS analogue).
//
// SCANNER shape (rendering_density.md §2.2 / Option (c) precedent): the wire
// returns a ranked LIST of (curve_family, tenor) extremes ordered by |z| of
// the 252d-rolling PX_LAST par-swap-rate z-score, NOT a single time series —
// so this module's compact view is a top-N TABLE and its extended view is a
// universe-scan canvas (distribution histogram + ranked detail table).
//
//   - methodology_exposure.md §5 standalone-bridge contract (own typed-detail
//     endpoint at /api/v1/rates/detail/ois-scanner + own surfaces; no shared
//     typedView)
//   - rendering_density.md §1 dual-view mandate (BOTH buildExtended +
//     buildCompact REQUIRED)
//
// Sibling (mirrored file-for-file): ../scan_extremes_tool/module.ts.
//
// Per FM7 (pure-spec assembly): this file exports a pure value.  No side
// effects, no global mutation, no register() calls.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'scan_ois_extremes_tool',

  // FM3 — tier claims.  Mirrors the sovereign twin's tier shape:
  // ``manifest_typed_view`` because the backend gate lists this tool in
  // ``_MANIFEST_ONLY_BUILD_TOOLS`` (typed-detail endpoint exists; the
  // workflow bridge does not dispatch it).
  tiers: ['manifest_typed_view', 'custom_build_surface'],

  // FM5 — display metadata.
  displayName: 'Scan OIS Extremes',
  category: 'screening',
  oneLineSummary:
    'Scans every OIS instrument in the database, ranks the top-N by absolute 252-day z-score and returns rate, daily change, z-score, percentile, and signal label per row.  OIS analogue of scan_extremes.',

  // FM9 — STANDALONE pattern (methodology_exposure.md §5): both Build
  // surfaces fetch the per-tool typed-detail endpoint
  // ``/api/v1/rates/detail/ois-scanner``.
  typedView: null,
  richModel: false,

  // FM8 — dual Build-side surfaces (rendering_density.md §5).  ``build`` is
  // kept === buildExtended for the legacy VirtualPrimitiveCanvas dispatcher
  // (transitional alias until the dispatcher reads ``buildExtended``).
  surfaces: {
    build: BuildExtended,
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM6 — unsupportedReason is REQUIRED by the framework invariant when
  // the runtime-status tier is ``manifest_typed_view`` (see
  // ``src/modules/__test-utils.ts`` invariant #8).  Mirrors the sovereign
  // twin's copy shape: the typed-detail endpoint + dual-view Build surfaces
  // are the live read; the workflow bridge cannot dispatch the tool.
  unsupportedReason: {
    label: 'scan_ois_extremes',
    reason:
      'Manifest-declared tool with a backend implementation behind a typed-detail endpoint (`/api/v1/rates/detail/ois-scanner`).  The workflow bridge does not dispatch it (snapshot-shape scanner output); the dual-view Build surfaces in this module are the live read.',
    whatWorksNow:
      'The per-tool BuildExtended (universe-scan canvas) + BuildCompact (top-N table) surfaces in Build render this tool against its typed-detail endpoint.  Ask handoff works too — the tool is in `_MANIFEST_ONLY_BUILD_TOOLS` on the backend gate.',
  },
};
