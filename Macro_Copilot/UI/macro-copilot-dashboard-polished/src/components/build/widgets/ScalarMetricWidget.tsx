// ============================================================================
// ScalarMetricWidget — renderer for ScalarMetric artifacts.
// ----------------------------------------------------------------------------
// Renders the artifact's single numeric value (or value + units when
// the units are present) as the big-number on the card.  ScalarMetric
// is uncommon in V1 — most analysis outputs are Series / Panel — but
// the contract supports it so we ship a clean renderer rather than
// fall through to the generic fallback.
//
// The ArtifactSummary doesn't currently carry the scalar payload
// directly (it lives in the artifact's body, fetched lazily).  Until
// PR B's per-card detail flow lands, we surface a "Scalar ready"
// state with the units + row_count (which on ScalarMetric is the
// number of dimensions, usually 1).
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';

const ScalarMetricWidget: NodeRenderer = ({ artifact }) => {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-start px-5 py-4">
      <div className="kicker text-fg-muted">Scalar metric</div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="font-serif-display text-[26px] font-light leading-none text-fg-primary">
          {artifact.units ?? '—'}
        </span>
        {artifact.row_count != null && (
          <span className="text-[11px] text-fg-muted">
            {artifact.row_count} dim{artifact.row_count === 1 ? '' : 's'}
          </span>
        )}
      </div>
      <p className="mt-3 text-[11px] leading-[1.5] text-fg-secondary">
        Scalar value is available in the artifact payload.  Click the
        card to open the detail view.
      </p>
    </div>
  );
};

registerArtifactRenderer('ScalarMetric', ScalarMetricWidget);
export { ScalarMetricWidget };
