// ============================================================================
// PCAWidget — per-tool renderer for ``pca_yield_curve_tool``.
// ----------------------------------------------------------------------------
// PR C — PCA on a curve outputs a Series that's "PC1 score through
// time" or "level loading".  A generic Series renderer would hide the
// PCA-specific context (how many components, which curve, what
// frequency), so this widget surfaces those explicitly in the subtitle
// and reframes the stats as "PC1 today / PC1 sigma / Components".
// ============================================================================

import { useMemo } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { ArtifactSparkline } from './shared/ArtifactSparkline';
import {
  ToolParamSubtitle,
  extractToolParam,
} from './shared/ToolParamSubtitle';

const PCAWidget: NodeRenderer = ({ node, artifact, category, size }) => {
  const stats = useMemo(() => computePcStats(artifact.preview_values), [artifact]);

  const curve = extractToolParam(node.params, 'curve_family')
    ?? extractToolParam(node.params, 'instrument');
  const components = extractToolParam(node.params, 'n_components')
    ?? extractToolParam(node.params, 'components');
  const component = extractToolParam(node.params, 'component')
    ?? extractToolParam(node.params, 'pc_index')
    ?? '1';

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ToolParamSubtitle
        chips={[
          curve ? { label: 'Curve', value: curve } : null,
          { label: 'Component', value: `PC${component}` },
          components ? { label: 'Total comps', value: components } : null,
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
        <StatCell label="PC today" value={stats.current} highlight />
        <StatCell label="σ" value={stats.sigma} />
        <StatCell label="Rows" value={stats.count} />
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
      PCA component series has no preview values yet
    </div>
  );
}

interface PcStats {
  hasData: boolean;
  current: string;
  sigma: string;
  count: number;
}

function computePcStats(values: Array<number | null>): PcStats {
  const live = values.filter(
    (v): v is number => v != null && !Number.isNaN(v),
  );
  if (live.length === 0) {
    return { hasData: false, current: '—', sigma: '—', count: 0 };
  }
  const last = live[live.length - 1];
  const mean = live.reduce((s, v) => s + v, 0) / live.length;
  const variance =
    live.reduce((s, v) => s + (v - mean) ** 2, 0) / live.length;
  const sigma = Math.sqrt(variance);
  return {
    hasData: true,
    current: last.toFixed(3),
    sigma: sigma.toFixed(3),
    count: live.length,
  };
}

registerToolRenderer(
  { artifactType: 'Series', toolName: 'pca_yield_curve_tool' },
  PCAWidget,
);
registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_pca_yield_curve_tool' },
  PCAWidget,
);

export { PCAWidget };
