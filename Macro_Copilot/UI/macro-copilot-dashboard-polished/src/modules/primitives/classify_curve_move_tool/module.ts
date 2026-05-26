// ============================================================================
// src/modules/primitives/classify_curve_move_tool/module.ts — Stage 4d.
// ----------------------------------------------------------------------------
// Stage 4a — typed-view module: ``custom_build_surface`` ships at
// ``surfaces/BuildSurface.tsx``; routes via ``typedView = 'regime'``.
// Stage 4d — Monitor catalog: one curve-classifier widget for the
// bento dashboard.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import ResultRenderer from './surfaces/ResultRenderer';
import { CurveClassifierWidget } from './surfaces/monitor/CurveClassifierWidget';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'classify_curve_move_tool',
  tiers: ['workflow_incompatible', 'custom_build_surface', 'monitor_surface'],
  displayName: 'Classify Curve Move',
  category: 'forwards_classify',
  oneLineSummary:
    'Deterministically classify a two-point sovereign curve move over a discrete lookback into one of six canonical labels.',
  typedView: 'regime',
  surfaces: { resultRenderer: ResultRenderer },
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
  unsupportedReason: {
    label: 'classify_curve_move',
    reason: 'Backend declares this tool in `WORKFLOW_INCOMPATIBLE_TOOLS`.  Backend rationale: Categorical output (BULL_STEEPENER / BEAR_FLATTENER / PARALLEL_SHIFT / TWIST regime label) + supporting numeric evidence; not a ``Series`` or ``Panel`` artifact. Bridge support for categorical outputs would let this register.',
    whatWorksNow: 'Ask can run the tool via MCP; a bespoke Build typed-view may exist via the rates typed-detail endpoints.',
  },
};
