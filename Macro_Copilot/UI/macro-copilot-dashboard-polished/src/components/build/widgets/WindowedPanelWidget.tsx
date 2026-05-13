// ============================================================================
// WindowedPanelWidget — renderer for WindowedPanel artifacts.
// ----------------------------------------------------------------------------
// WindowedPanel is the output of ``event_windows`` — a 3D shape
// indexed by ``(event_date, offset, column)`` flattened to a 2D
// "all-event-windows stacked" representation on the wire.
//
// V1 render: row count + offset-range strip + sparkline-of-first-
// column as a directional smell.  A faithful heatmap or bar-by-offset
// view requires the full payload; that flow ships in PR B.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { ArtifactSparkline } from './shared/ArtifactSparkline';

const WindowedPanelWidget: NodeRenderer = ({ artifact, category, size }) => {
  const rowCount = artifact.row_count ?? 0;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-5 pt-3 pb-1">
        <span className="text-[10.5px] text-fg-muted">
          Event-windowed panel
          {rowCount > 0 ? ` · ${rowCount.toLocaleString()} rows` : null}
        </span>
      </div>
      <ArtifactSparkline
        artifact={artifact}
        category={category}
        height={size === 'small' ? 56 : 88}
      />
    </div>
  );
};

registerArtifactRenderer('WindowedPanel', WindowedPanelWidget);
export { WindowedPanelWidget };
