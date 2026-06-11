// ============================================================================
// src/components/shared/build/model/ModelKpiStrip.tsx — model hero KPIs.
// ----------------------------------------------------------------------------
// Consolidation target #4: the rich-model grammar's hero strip is the
// SAME metric-tile component the simple primitives use
// (``PrimitiveMetrics``) — lifted here under the model grammar's name
// so PCA reads as consistently as curve_spread (P3, P10: one tile
// implementation, two entry points).  Model defaults differ only in
// density (4 columns — model heroes are fewer, larger reads).
//
// A model's module maps its Output onto ``MetricItem[]`` in its own
// surfaces folder; this shared layer never branches on the model
// (FP13).
// ============================================================================

import {
  PrimitiveMetrics,
  type MetricItem,
} from '@/components/build/primitive/PrimitiveMetrics';

export type { MetricItem };

export interface ModelKpiStripProps {
  items: MetricItem[];
  title?: string;
  /** Model heroes default to 4 columns (vs the primitive default 6). */
  desktopCols?: 4 | 5 | 6;
}

export function ModelKpiStrip({
  items,
  title,
  desktopCols = 4,
}: ModelKpiStripProps) {
  return (
    <PrimitiveMetrics items={items} title={title} desktopCols={desktopCols} />
  );
}
