// ============================================================================
// AttributionPreviewWidget — per-tool persisted-artifact card.
// ----------------------------------------------------------------------------
// PR1 (new plan) — attribution's output_class is pure-snapshot (no
// time_series field).  The substrate's Series bridge can't lift a
// time_series from a pure-snapshot output, so today the persisted-
// artifact path doesn't exist end-to-end.  The widget renders an
// honest "snapshot view not persistable today" callout instead of
// the legacy ``AttributionRenderer`` (which expected live-run
// ``current_metrics``).
//
// Both ``Series`` and ``Panel`` per-tool registrations are preserved
// so the persisted-widget surface stays the same.  If a future
// extension persists attribution as a Panel snapshot, the adapter
// can branch to a Panel-shape rendering with minimal change.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { RichModelWidget } from './shared/RichModelWidget';

const AttributionPreviewWidget: NodeRenderer = (props) => (
  <RichModelWidget
    {...props}
    toolName="calculate_yield_change_attribution_pca_tool"
  />
);

registerToolRenderer(
  {
    artifactType: 'Series',
    toolName: 'calculate_yield_change_attribution_pca_tool',
  },
  AttributionPreviewWidget,
);
registerToolRenderer(
  {
    artifactType: 'Panel',
    toolName: 'calculate_yield_change_attribution_pca_tool',
  },
  AttributionPreviewWidget,
);

export { AttributionPreviewWidget };
