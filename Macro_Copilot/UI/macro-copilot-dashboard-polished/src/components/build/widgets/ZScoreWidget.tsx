// ============================================================================
// ZScoreWidget — per-tool renderer for ``zscore_custom_tool`` Series outputs.
// ----------------------------------------------------------------------------
// PR C — the user's #1 concern: every primitive should render as its
// OWN polished card rather than a generic Series strip.  The z-score
// surface needs three things the generic ``SeriesWidget`` doesn't
// provide:
//
//   1. A param subtitle (window, instrument) so a reader can scan a
//      row of z-score cards and tell them apart without opening the
//      Parameters tab.
//   2. The current z value pulled into a prominent slot — that's the
//      "is this signal live?" number a trader actually looks at.
//   3. Implicit ±1σ / ±2σ context.  We render the threshold band as
//      a thin horizontal rule + a "thresholds" stat so readers know
//      what region the current value sits in even when the sparkline
//      compresses the y-axis.
//
// Falls through to a sparkline-less layout when ``preview_values`` is
// empty (rare — z-score outputs are dense by construction — but the
// renderer must stay branchless against artifact shape).
// ============================================================================

import { useMemo } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { ArtifactSparkline } from './shared/ArtifactSparkline';
import {
  ToolParamSubtitle,
  extractToolParam,
} from './shared/ToolParamSubtitle';

const ZScoreWidget: NodeRenderer = ({ node, artifact, category, size }) => {
  const stats = useMemo(() => computeZStats(artifact.preview_values), [artifact]);

  const window = extractToolParam(node.params, 'z_score_window_days')
    ?? extractToolParam(node.params, 'window_days');
  const tenor = extractToolParam(node.params, 'tenor');
  const curve = extractToolParam(node.params, 'curve_family')
    ?? extractToolParam(node.params, 'instrument');

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ToolParamSubtitle
        chips={[
          curve ? { label: 'Curve', value: curve } : null,
          tenor ? { label: 'Tenor', value: tenor } : null,
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

      {/* Threshold legend — quick mental anchor for ±1σ / ±2σ */}
      <div className="mx-5 mt-1.5 mb-2 flex items-center gap-3 text-[10px] text-fg-faint">
        <ThresholdSwatch tone="amber" /> ±1σ
        <ThresholdSwatch tone="rose" /> ±2σ
      </div>

      <div className="grid grid-cols-3 gap-x-4 gap-y-2 border-t border-line-soft px-5 py-3">
        <StatCell label="Current z" value={stats.current ?? '—'} accent={stats.accent} />
        <StatCell label="Max |z|" value={stats.peakAbs ?? '—'} />
        <StatCell label="Rows" value={stats.count} />
      </div>
    </div>
  );
};

function StatCell({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent?: 'amber' | 'rose' | null;
}) {
  const tone =
    accent === 'rose'
      ? 'text-rose-300'
      : accent === 'amber'
        ? 'text-amber-300'
        : 'text-fg-primary';
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-[9.5px] font-medium uppercase tracking-[0.16em] text-fg-faint">
        {label}
      </span>
      <span className={`font-mono text-[13px] tracking-[-0.005em] ${tone}`}>
        {value}
      </span>
    </div>
  );
}

function ThresholdSwatch({ tone }: { tone: 'amber' | 'rose' }) {
  return (
    <span
      aria-hidden
      className={
        tone === 'amber'
          ? 'h-px w-4 bg-amber-400/70'
          : 'h-px w-4 bg-rose-400/70'
      }
    />
  );
}

function NoData() {
  return (
    <div className="mx-5 my-2 rounded-md border border-dashed border-line-soft px-3 py-4 text-center text-[10.5px] text-fg-faint">
      Z-score series has no preview values yet
    </div>
  );
}

interface ZStats {
  hasData: boolean;
  current: string | null;
  peakAbs: string | null;
  count: number;
  accent: 'amber' | 'rose' | null;
}

function computeZStats(values: Array<number | null>): ZStats {
  const live = values.filter(
    (v): v is number => v != null && !Number.isNaN(v),
  );
  if (live.length === 0) {
    return { hasData: false, current: null, peakAbs: null, count: 0, accent: null };
  }
  const last = live[live.length - 1];
  const peak = live.reduce((m, v) => (Math.abs(v) > Math.abs(m) ? v : m), 0);
  const accent: 'amber' | 'rose' | null =
    Math.abs(last) >= 2 ? 'rose' : Math.abs(last) >= 1 ? 'amber' : null;
  return {
    hasData: true,
    current: last.toFixed(2),
    peakAbs: Math.abs(peak).toFixed(2),
    count: live.length,
    accent,
  };
}

// Register against BOTH the simple primitive name and the workflow-namespaced
// variant.  The substrate persists ``tool_name`` straight from the MCP tool
// definition; the workflow tool wrapper persists "calculate_zscore_custom_tool"
// while the bare MCP tool registers as "zscore_custom_tool".
registerToolRenderer(
  { artifactType: 'Series', toolName: 'zscore_custom_tool' },
  ZScoreWidget,
);
registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_zscore_custom_tool' },
  ZScoreWidget,
);

export { ZScoreWidget };
