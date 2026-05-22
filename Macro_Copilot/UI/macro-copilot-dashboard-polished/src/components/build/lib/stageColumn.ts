// ============================================================================
// stageColumn.ts — column-label mapping for a StageCategory.
// ----------------------------------------------------------------------------
// Single source of truth for the kicker text that sits above (DAG
// strip) or inside (NodeWidgetCard header) every stage card.  Keeps
// the vocabulary identical between surfaces so the DAG view and the
// Results view name a stage the same way.
// ============================================================================

import type { StageCategory } from './buildTypes';

export function columnLabelForCategory(category: StageCategory): string {
  switch (category) {
    case 'input':
      return 'Primitive';
    case 'transform':
      return 'Operator';
    case 'output':
      return 'Output';
  }
}
