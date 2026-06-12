// ============================================================================
// src/modules/primitives/classify_curve_move_tool/module.ts
// ----------------------------------------------------------------------------
// Consolidation (G-3.1c) — migrated from the legacy typed-view route
// (``typedView: 'regime'`` + ``surfaces.resultRenderer``) to the
// dual-view standard.  THIS RETIRES THE LAST ``typedView`` CLAIM IN
// THE CODEBASE — the PrimitiveViewKind routing substrate is now dead
// code awaiting deletion (consolidation target #5).
//   - methodology_exposure.md §5 standalone bridge (the existing
//     /api/v1/rates/detail/regime endpoint + own surfaces; the legacy
//     shared RegimeView route is unclaimed)
//   - rendering_density.md §1 dual-view mandate (buildExtended +
//     buildCompact both REQUIRED)
//
// The Monitor widget is PRESERVED VERBATIM — it reads the
// pre-aggregated ``RatesDataContext`` feed (/regimes), not
// /detail/regime.  The runtime tier stays ``workflow_incompatible``
// (categorical output is not bridge-composable) with the backend
// rationale verbatim; capability tiers free-combine with it.
// Per FM7 (pure-spec assembly): exports a pure value; no side effects.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildExtended from './surfaces/BuildExtended';
import BuildCompact from './surfaces/BuildCompact';
import { CurveClassifierWidget } from './surfaces/monitor/CurveClassifierWidget';

export const MODULE: PrimitiveModuleSpec = {
  // FM1 — identity (folder name === toolName)
  toolName: 'classify_curve_move_tool',

  // FM3 — tier claims: workflow-incompatible runtime status (the
  // categorical output can't bridge into workflows) + the dual-view
  // Build surfaces + the existing Monitor widget.
  tiers: ['workflow_incompatible', 'custom_build_surface', 'monitor_surface'],

  // FM5 — display metadata
  displayName: 'Classify Curve Move',
  category: 'forwards_classify',
  oneLineSummary:
    'Deterministically classify a two-point sovereign curve move over a discrete lookback into one of six canonical labels.',

  // FM9 — STANDALONE pattern (methodology_exposure.md §5): the
  // typedView('regime') route is RETIRED; the module owns its own
  // dual-view surfaces on the same /detail/regime bridge.
  typedView: null,
  richModel: false,

  // FM8 — dual Build-side surfaces (rendering_density.md §5).
  // ``build`` is kept === buildExtended for the legacy
  // VirtualPrimitiveCanvas dispatcher (transitional alias).
  surfaces: {
    build: BuildExtended,
    buildExtended: BuildExtended,
    buildCompact: BuildCompact,
  },

  // FM5c — Monitor catalog widget (preserved verbatim; reads the
  // pre-aggregated /regimes feed, not the typed-detail bridge).
  monitorWidgets: [
    {
      id: 'curve_classifier',
      label: 'Curve Classifier',
      description:
        'Classifies daily / weekly curve moves as steepener, flattener, twist, or parallel shift across G4 curves. Backed by classify_curve_move_tool.',
      category: 'analysis',
      defaultSize: 'medium',
      allowedSizes: ['medium'],
      parameterized: false,
      component: CurveClassifierWidget,
    },
  ],

  // FM5 — defaults mirror the typed-detail bridge's flat wire shape.
  defaultParams: {
    curve_family: 'UST',
    front_tenor: '2Y',
    back_tenor: '10Y',
    lookback_period: '1d',
  },

  // FM6 — workflow_incompatible rationale (backend copy verbatim).
  unsupportedReason: {
    label: 'classify_curve_move',
    reason:
      'Backend declares this tool in `WORKFLOW_INCOMPATIBLE_TOOLS`.  Backend rationale: Categorical output (BULL_STEEPENER / BEAR_FLATTENER / PARALLEL_SHIFT / TWIST regime label) + supporting numeric evidence; not a ``Series`` or ``Panel`` artifact. Bridge support for categorical outputs would let this register.',
    whatWorksNow:
      'Ask can run the tool via MCP; Build ships the full dual-view surface on the /detail/regime standalone bridge.',
  },
};
