// ============================================================================
// src/modules/primitives/classify_curve_move_tool/module.ts — Stage 3 scaffold.
// ----------------------------------------------------------------------------
// Minimum-viable module spec — declares only the runtime-status tier.
// Stage 4 PRs add capability tiers (custom_build_surface, etc.) when the
// matching surface code moves into this folder from its legacy location.
// See THESIS.md for the design intent + planned Stage-4 capabilities.
// ============================================================================

import type { PrimitiveModuleSpec } from '../../types';

export const MODULE: PrimitiveModuleSpec = {
  toolName: 'classify_curve_move_tool',
  tiers: ['workflow_incompatible'],
  displayName: 'classify_curve_move',
  category: 'forwards_classify',
  oneLineSummary: 'Deterministically classify a two-point sovereign curve move over a discrete lookback into one of six canonical labels.',
  unsupportedReason: {
    label: 'classify_curve_move',
    reason: 'Backend declares this tool in `WORKFLOW_INCOMPATIBLE_TOOLS`.  Backend rationale: Categorical output (BULL_STEEPENER / BEAR_FLATTENER / PARALLEL_SHIFT / TWIST regime label) + supporting numeric evidence; not a ``Series`` or ``Panel`` artifact. Bridge support for categorical outputs would let this register.',
    whatWorksNow: 'Ask can run the tool via MCP; a bespoke Build typed-view may exist via the rates typed-detail endpoints.',
  },
};
