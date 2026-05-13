// ============================================================================
// CurveSpreadWidget — per-tool renderer for ``calculate_curve_spread_tool``.
// ----------------------------------------------------------------------------
// PR C — the curve-spread surface ("2s10s") wants:
//
//   - A leg labelling row (Front / Back / Curve) so the spread definition
//     is on the face of the card.
//   - The current value highlighted, formatted in bps when units allow.
//   - First / last + a simple range strip so a glance answers "how
//     extreme is current vs history?"
//
// Pulls the front/back tenors out of ``node.params`` via the param
// subtitle helper.  Falls back to the generic sparkline layout when the
// substrate persisted a node without curve metadata (legacy paths).
// ============================================================================

import { useMemo } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { ArtifactSparkline } from './shared/ArtifactSparkline';
import {
  ToolParamSubtitle,
  extractToolParam,
} from './shared/ToolParamSubtitle';

const CurveSpreadWidget: NodeRenderer = ({
  node,
  artifact,
  category,
  size,
}) => {
  const stats = useMemo(
    () => computeSpreadStats(artifact.preview_values, artifact.units),
    [artifact],
  );

  const curve = extractToolParam(node.params, 'curve_family')
    ?? extractToolParam(node.params, 'instrument')
    ?? extractToolParam(node.params, 'curve');
  const front = extractToolParam(node.params, 'front_tenor')
    ?? extractToolParam(node.params, 'short_tenor');
  const back = extractToolParam(node.params, 'back_tenor')
    ?? extractToolParam(node.params, 'long_tenor');
  const spreadLabel = front && back ? `${front}–${back}` : undefined;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ToolParamSubtitle
        chips={[
          curve ? { label: 'Curve', value: curve } : null,
          spreadLabel ? { label: 'Spread', value: spreadLabel } : null,
          artifact.units ? { label: 'Units', value: artifact.units } : null,
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
        <StatCell label="Current" value={stats.current} highlight />
        <StatCell label="52w range" value={stats.range} />
        <StatCell label="Σ rows" value={stats.count} />
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
  value: string | number;
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
      Spread series has no preview values yet
    </div>
  );
}

interface SpreadStats {
  hasData: boolean;
  current: string;
  range: string;
  count: number;
}

function computeSpreadStats(
  values: Array<number | null>,
  units: string | null,
): SpreadStats {
  const live = values.filter(
    (v): v is number => v != null && !Number.isNaN(v),
  );
  if (live.length === 0) {
    return { hasData: false, current: '—', range: '—', count: 0 };
  }
  const last = live[live.length - 1];
  const min = Math.min(...live);
  const max = Math.max(...live);
  return {
    hasData: true,
    current: formatBps(last, units),
    range: `${formatBps(min, units)} → ${formatBps(max, units)}`,
    count: live.length,
  };
}

function formatBps(value: number, units: string | null): string {
  const u = (units ?? '').toLowerCase();
  if (u === 'bps') return `${value.toFixed(1)} bps`;
  if (u === 'percent' || u === 'pct') return `${value.toFixed(3)}%`;
  return value.toFixed(2);
}

registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_curve_spread_tool' },
  CurveSpreadWidget,
);
registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_cross_market_spread_tool' },
  CurveSpreadWidget,
);
registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_butterfly_tool' },
  CurveSpreadWidget,
);

export { CurveSpreadWidget };
