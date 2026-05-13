// ============================================================================
// SeriesWidget — default renderer for Series artifacts.
// ----------------------------------------------------------------------------
// The most common shape on the Build canvas — anything that emits a
// 1-D time series (yields, spreads, z-scores, forwards) flows here.
//
// Body composition (top → bottom):
//   1. Description line     — pulled from the artifact summary's
//                             ``description`` if present, else
//                             elided.
//   2. Sparkline            — drawn from ``preview_index`` /
//                             ``preview_values``; toned to match
//                             the parent stage's category.
//   3. ArtifactStatsBlock   — first / last / row count.
//
// The footer (provenance) is rendered by the parent ``NodeWidgetCard``;
// renderers focus on the body only.
// ============================================================================

import { useMemo } from 'react';
import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { ArtifactSparkline } from './shared/ArtifactSparkline';
import { ArtifactStatsBlock } from './shared/ArtifactStatsBlock';

const SeriesWidget: NodeRenderer = ({ artifact, category, size }) => {
  const hasPreview = useMemo(
    () => (artifact.preview_values ?? []).some((v) => v != null && !Number.isNaN(v)),
    [artifact],
  );
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Sparkline strip */}
      <div className="pt-1">
        {hasPreview ? (
          <ArtifactSparkline
            artifact={artifact}
            category={category}
            height={size === 'small' ? 56 : 96}
          />
        ) : (
          <EmptyPreview />
        )}
      </div>

      <ArtifactStatsBlock
        artifact={artifact}
        compact={size === 'small'}
      />
    </div>
  );
};

function EmptyPreview() {
  return (
    <div className="mx-5 my-2 rounded-md border border-dashed border-line-soft px-3 py-4 text-center text-[10.5px] text-fg-faint">
      No preview values available
    </div>
  );
}

// Self-register on import.
registerArtifactRenderer('Series', SeriesWidget);

export { SeriesWidget };
