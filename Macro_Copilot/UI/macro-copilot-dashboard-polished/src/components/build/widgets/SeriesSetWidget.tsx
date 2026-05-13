// ============================================================================
// SeriesSetWidget — renderer for multi-series artifacts (SeriesSet).
// ----------------------------------------------------------------------------
// A SeriesSet artifact carries N keyed Series.  The substrate's
// ArtifactSummary exposes a single preview pair (preview_index +
// preview_values) which represents the FIRST member by convention;
// the per-member detail lives in the artifact payload and isn't
// fetched on the workspace-summary path.
//
// V1 strategy: render the lead series as a sparkline + state that
// additional members exist.  PR B can extend this to fetch the full
// payload on hover / click and stack a small-multiples chart.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { ArtifactSparkline } from './shared/ArtifactSparkline';
import { ArtifactStatsBlock } from './shared/ArtifactStatsBlock';

const SeriesSetWidget: NodeRenderer = ({ artifact, category, size }) => {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-5 pt-3 pb-1">
        <span className="text-[10.5px] text-fg-muted">
          Multi-series set — lead member shown below.
        </span>
      </div>
      <ArtifactSparkline
        artifact={artifact}
        category={category}
        height={size === 'small' ? 56 : 88}
      />
      <ArtifactStatsBlock artifact={artifact} compact={size === 'small'} />
    </div>
  );
};

registerArtifactRenderer('SeriesSet', SeriesSetWidget);
export { SeriesSetWidget };
