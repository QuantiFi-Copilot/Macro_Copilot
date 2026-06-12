// ============================================================================
// src/modules/primitives/policy_futures_get_futures_strip_snapshot_tool/module.ts
// ----------------------------------------------------------------------------
// Dual-view build for the policy_futures whole-strip snapshot.  Under
// the new standards:
//   - methodology_exposure.md §5 standalone-bridge (own typed-detail
//     endpoint at /api/v1/rates/detail/policy-futures-strip-snapshot +
//     own surfaces; no shared typedView)
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// Shape: WHOLE-STRIP SNAPSHOT — one row per configured strip position
// on ONE curve_family, all aligned to a single as_of_date (the date on
// which "the strip is steep / flat / inverted" makes sense).  No time
// series on the wire; the extended view's primary zone is a cross-
// sectional strip-curve read, the compact view is a table-shaped card
// per the scan_extremes_tool guardrail.  See THESIS.md.
//
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'policy_futures_get_futures_strip_snapshot_tool',

  // FM3 — tier claims:
  //   * generic_runnable — backend ships in _PRIMITIVE_SPECS
  //   * custom_build_surface — dual-view Build (rendering_density.md §1)
  // No monitor_surface claim: the registry was checked for an existing
  // strip tile (none exists) and no pilot-pattern widget ships in this
  // migration — claiming the tier without a widget would violate FM8.
  // See THESIS.md Q4 for the trigger that adds it.
  tiers: ['generic_runnable', 'custom_build_surface'],

  // FM5 — display metadata (kept from the Stage 3 scaffold).
  displayName: 'Policy Futures Futures Strip Snapshot',
  category: 'snapshots',
  oneLineSummary: 'Whole-strip side-by-side snapshot for ONE policy-futures.',

  // FM9 — STANDALONE pattern per methodology_exposure.md §5: no shared
  // typedView; not a rich-model builder.
  typedView: null,
  richModel: false,

  // FM8 — dual Build-side surfaces (rendering_density.md §5).  ``build``
  // is kept === buildExtended as the transitional alias for the legacy
  // VirtualPrimitiveCanvas dispatcher that reads ``surfaces.build``.
  surfaces: {
    build: BuildExtended,
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5 — defaults mirror the backend Input's one structural field
  // (curve_family).  as_of_date / the two Bloomberg field overrides
  // default to the backend None-sentinels (data-max anchor / YAML
  // defaults), so they are intentionally NOT seeded here.
  defaultParams: {
    curve_family: 'SOFR_FUT',
  },
};
