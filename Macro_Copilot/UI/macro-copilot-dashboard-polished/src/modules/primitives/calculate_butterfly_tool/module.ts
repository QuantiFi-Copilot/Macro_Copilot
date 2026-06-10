// ============================================================================
// src/modules/primitives/calculate_butterfly_tool/module.ts
// ----------------------------------------------------------------------------
// Migration dispatch — converted from the legacy typed-renderer pattern
// (``typedView: 'butterfly'`` + ``surfaces.resultRenderer``) to the new
// dual-view + standalone-bridge contract under:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail endpoint
//     at /api/v1/rates/detail/butterfly + own surfaces; no shared typedView)
//   - rendering_density.md §1 dual-view mandate (buildExtended + buildCompact
//     both REQUIRED)
//
// Runtime tier UNCHANGED: ``manifest_typed_view`` is preserved because the
// backend's ``_PRIMITIVE_SPECS`` membership for this tool is unchanged — it
// remains a ``_MANIFEST_ONLY_BUILD_TOOLS`` entry behind a typed-detail
// endpoint, not a generic_runnable primitive.  Per MIGRATION_RULES §4 step 6
// + §8 anti-patterns the tier set must mirror backend reality; swapping to
// generic_runnable would be a structural violation.  The dual-view + standalone
// bridge IS the live surface; the ``unsupportedReason`` copy is updated to
// reflect that (the field is still required by the test infrastructure for
// modules claiming manifest_typed_view — invariant 8 in __test-utils.ts).
//
// No Monitor widget is added — the pre-migration module did not claim
// ``monitor_surface`` and the design_guardrails are explicit ("NO monitor
// widget — do NOT add one").
//
// Design reference: mockups/Compact.png + mockups/Extended.png.
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'calculate_butterfly_tool',

  // FM3 — tier claims (UNCHANGED from the pre-migration set; the backend's
  // _PRIMITIVE_SPECS membership is unchanged so the tier set mirrors backend
  // reality):
  //   * manifest_typed_view — backend's _MANIFEST_ONLY_BUILD_TOOLS gate, not
  //     in _PRIMITIVE_SPECS; the workflow bridge cannot dispatch it.  The
  //     dual-view + standalone-bridge IS the live surface.
  //   * custom_build_surface — dual-view Build (rendering_density.md §1).
  tiers: ['manifest_typed_view', 'custom_build_surface'],

  // FM5 — display metadata
  displayName: 'Butterfly',
  category: 'curve_shape',
  oneLineSummary:
    'Three-point curvature on a sovereign curve — (2 × belly − short − long) × 100 bps — with rolling z-score, trailing 252d range, wing-spread components, and full time series.',

  // FM9 — STANDALONE pattern (methodology_exposure.md §5): no shared typedView.
  typedView: null,
  richModel: false,

  // FM8 — dual Build-side surfaces (rendering_density.md §5).  ``build`` is
  // kept === buildExtended for the legacy VirtualPrimitiveCanvas dispatcher.
  surfaces: {
    build: BuildExtended,
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM6 — runtime-status reason.  Required by the test infrastructure's
  // invariant 8 for any module claiming ``manifest_typed_view`` (see
  // src/modules/__test-utils.ts:265).  The honest copy explains that the
  // dual-view + standalone-bridge IS the supported surface (post-migration).
  unsupportedReason: {
    label: 'calculate_butterfly',
    reason:
      'Manifest-declared tool with a backend implementation behind a typed-detail endpoint (`/api/v1/rates/detail/butterfly`).  Not in `_PRIMITIVE_SPECS` so the workflow bridge cannot dispatch it through the generic `/run` route.',
    whatWorksNow:
      'The dual-view Build surface (extended + compact) renders this tool against its standalone-bridge typed-detail endpoint at `/api/v1/rates/detail/butterfly`.  Ask handoff works too — the tool is in `_MANIFEST_ONLY_BUILD_TOOLS` on the backend gate.',
  },
};
