// ============================================================================
// RollingRegressionWidget — per-tool renderer for ``rolling_regression_tool``.
// ----------------------------------------------------------------------------
// PR C — rolling regression's output is "rolling β through time".  The
// reader's mental model is:
//
//   "what's β right now, how stable has it been, and against what
//    factor was this run?"
//
// So we surface:
//   - Param subtitle pulling out the factor name + window length.
//   - Sparkline of the rolling β series.
//   - Current β + range stats below.
//
// The widget assumes a Series artifact; rolling regression in V1 emits
// a univariate β series.  Once a future PR adds α / R² siblings we'll
// pivot this to read from a SeriesSet — that's a one-line registry
// retarget, no call-site change.
// ============================================================================

import { useMemo } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { ArtifactSparkline } from './shared/ArtifactSparkline';
import {
  ToolParamSubtitle,
  extractToolParam,
} from './shared/ToolParamSubtitle';

const RollingRegressionWidget: NodeRenderer = ({
  node,
  artifact,
  category,
  size,
}) => {
  const stats = useMemo(
    () => computeBetaStats(artifact.preview_values),
    [artifact],
  );

  const factor = extractToolParam(node.params, 'factor_name')
    ?? extractToolParam(node.params, 'factor')
    ?? extractToolParam(node.params, 'regressor');
  const target = extractToolParam(node.params, 'target_name')
    ?? extractToolParam(node.params, 'target');
  const window = extractToolParam(node.params, 'window_days')
    ?? extractToolParam(node.params, 'rolling_window');

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ToolParamSubtitle
        chips={[
          target ? { label: 'Target', value: target } : null,
          factor ? { label: 'Factor', value: factor } : null,
          window ? { label: 'Window', value: `${window}d` } : null,
        ]}
      />

      <div className="pt-1">
        {stats.hasData ? (
          <ArtifactSparkline
            artifact={artifact}
            category={category}
            height={size === 'small' ? 56 : 96}
          />
        ) : (
          <NoData />
        )}
      </div>

      <div className="grid grid-cols-3 gap-x-4 gap-y-2 border-t border-line-soft px-5 py-3">
        <StatCell label="Current β" value={stats.current} highlight />
        <StatCell label="β range" value={stats.range} />
        <StatCell label="Stability" value={stats.stability} />
      </div>
    </div>
  );
};

function StatCell({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-[9.5px] font-medium uppercase tracking-[0.16em] text-fg-faint">
        {label}
      </span>
      <span
        className={
          highlight
            ? 'font-mono text-[13.5px] tracking-[-0.005em] text-fg-primary'
            : 'font-mono text-[12px] text-fg-secondary'
        }
      >
        {value}
      </span>
    </div>
  );
}

function NoData() {
  return (
    <div className="mx-5 my-2 rounded-md border border-dashed border-line-soft px-3 py-4 text-center text-[10.5px] text-fg-faint">
      Rolling β series has no preview values yet
    </div>
  );
}

interface BetaStats {
  hasData: boolean;
  current: string;
  range: string;
  stability: string;
}

function computeBetaStats(values: Array<number | null>): BetaStats {
  const live = values.filter(
    (v): v is number => v != null && !Number.isNaN(v),
  );
  if (live.length === 0) {
    return { hasData: false, current: '—', range: '—', stability: '—' };
  }
  const last = live[live.length - 1];
  const min = Math.min(...live);
  const max = Math.max(...live);
  // Stability proxy = 1 - (range / max(|min|, |max|)).  Cheap heuristic
  // that hits ≈1.0 for a flat β and drops toward 0 as β oscillates;
  // good enough as a glance metric without computing actual std-dev.
  const denom = Math.max(Math.abs(min), Math.abs(max), 1e-9);
  const stability = Math.max(0, 1 - (max - min) / denom);
  return {
    hasData: true,
    current: last.toFixed(3),
    range: `${min.toFixed(2)} → ${max.toFixed(2)}`,
    stability: stability.toFixed(2),
  };
}

registerToolRenderer(
  { artifactType: 'Series', toolName: 'rolling_regression_tool' },
  RollingRegressionWidget,
);
registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_rolling_regression_tool' },
  RollingRegressionWidget,
);

export { RollingRegressionWidget };
