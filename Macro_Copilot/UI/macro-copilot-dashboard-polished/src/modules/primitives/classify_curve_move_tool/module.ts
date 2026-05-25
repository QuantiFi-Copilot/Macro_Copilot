// ============================================================================
// src/modules/primitives/classify_curve_move_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Stage 4a — typed-view module.  Claims ``custom_build_surface`` because
// the typed view (formerly ``src/components/build/primitive/`` legacy
// location) now ships at ``surfaces/BuildSurface.tsx`` in this folder.
// Routes via ``MODULE.typedView = 'regime'`` through the context
// decoder's TOOL_TO_VIEW derivation.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';
import BuildSurface from './surfaces/BuildSurface';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'classify_curve_move_tool',
  tiers: ['workflow_incompatible', 'custom_build_surface'],
  displayName: 'classify_curve_move',
  category: 'forwards_classify',
  oneLineSummary: 'Deterministically classify a two-point sovereign curve move over a discrete lookback into one of six canonical labels.',
  typedView: 'regime',
  surfaces: { build: BuildSurface },
  unsupportedReason: {
    label: 'classify_curve_move',
    reason: 'Backend declares this tool in `WORKFLOW_INCOMPATIBLE_TOOLS`.  Backend rationale: Categorical output (BULL_STEEPENER / BEAR_FLATTENER / PARALLEL_SHIFT / TWIST regime label) + supporting numeric evidence; not a ``Series`` or ``Panel`` artifact. Bridge support for categorical outputs would let this register.',
    whatWorksNow: 'Ask can run the tool via MCP; a bespoke Build typed-view may exist via the rates typed-detail endpoints.',
  },
};
