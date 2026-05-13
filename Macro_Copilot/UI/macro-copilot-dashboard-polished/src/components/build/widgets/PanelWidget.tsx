// ============================================================================
// PanelWidget — renderer for Panel artifacts.
// ----------------------------------------------------------------------------
// A Panel is a date-indexed multi-column dataframe (yields panel,
// financing-rate panel, backtest-summary table).  Two visual modes:
//
//   1. One-row Panel (e.g. a backtest summary)
//        → render as a metric-strip card: each column gets a kicker
//          + big-number cell.  This is the shape Mockup C's
//          "Backtest Summary" tile uses.
//
//   2. Multi-row Panel
//        → sparkline of the first numeric column + row count badge.
//          The full per-column data ships on the artifact's payload
//          (fetched lazily by PR B's "open in detail" flow).
//
// V1 uses the ArtifactSummary's ``preview_values`` as the first-column
// proxy.  When the preview is empty we render a "data ready, no
// preview" placeholder so the card still anchors visually.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { ArtifactSparkline } from './shared/ArtifactSparkline';
import { ArtifactStatsBlock } from './shared/ArtifactStatsBlock';

const PanelWidget: NodeRenderer = ({ artifact, category, size }) => {
  const rowCount = artifact.row_count ?? 0;

  // One-row Panels (e.g. metric summaries) get a different
  // treatment — no sparkline (a single point isn't a series).
  if (rowCount === 1) {
    return (
      <div className="flex min-h-0 flex-1 flex-col px-5 py-4">
        <div className="text-[10.5px] uppercase tracking-[0.16em] text-fg-muted">
          Summary
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="font-serif-display text-[26px] font-light leading-none text-fg-primary">
            1
          </span>
          <span className="text-[11px] text-fg-muted">row</span>
        </div>
        <p className="mt-3 text-[11px] leading-[1.5] text-fg-secondary">
          Per-column values surface in the detail view — open the
          card to see every metric the Panel carries.
        </p>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ArtifactSparkline
        artifact={artifact}
        category={category}
        height={size === 'small' ? 56 : 88}
      />
      <ArtifactStatsBlock artifact={artifact} compact={size === 'small'} />
    </div>
  );
};

registerArtifactRenderer('Panel', PanelWidget);
export { PanelWidget };
