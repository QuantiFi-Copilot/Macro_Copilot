// ============================================================================
// src/components/shared/build/model/index.ts — rich-model grammar barrel.
// ----------------------------------------------------------------------------
// Consolidation target #4: the shared, finance-blind UI grammar for
// rich-model results.  Modules compose these from their own surfaces
// folders; the shared layer never branches on which model (FP13).
//
//   ModelResultLayout  — section scaffold (hero → primary → secondary →
//                        diagnostics → methodology)
//   ModelKpiStrip      — hero KPI tiles (the PrimitiveMetrics lift)
//   MatrixTable        — loadings / coefficient matrices with the
//                        shared diverging shading
//   DecompositionBars  — variance shares + signed contributions
//   ModelSeriesPanel   — the one multi-line chart for factor scores /
//                        betas / residual z
//   QualityBadge       — model-fit health chips (ok / warning /
//                        degraded)
// ============================================================================

export { ModelResultLayout, type ModelResultLayoutProps } from './ModelResultLayout';
export { ModelKpiStrip, type ModelKpiStripProps, type MetricItem } from './ModelKpiStrip';
export { MatrixTable, type MatrixTableProps } from './MatrixTable';
export { DecompositionBars, type DecompositionBarsProps } from './DecompositionBars';
export { ModelSeriesPanel, type ModelSeriesPanelProps } from './ModelSeriesPanel';
export {
  QualityBadge,
  qualityLevelForConditionFlag,
  qualityLevelForFlag,
  type QualityBadgeProps,
} from './QualityBadge';
export {
  MODEL_TONE_CYCLE,
  modelToneAt,
  type DecompositionEntry,
  type MatrixRow,
  type ModelSeries,
  type QualityLevel,
} from './types';
