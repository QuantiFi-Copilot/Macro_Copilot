// ============================================================================
// AttributionPreviewWidget — per-tool renderer for yield-change attribution.
// ----------------------------------------------------------------------------
// Phase R3 replaces the thin "preview" widget with the rich
// ``AttributionRenderer`` (component-level contribution waterfall +
// residual breakdown from the legacy model-workspace).  Registered for
// both ``Series`` and ``Panel`` artifact shapes — the attribution tool
// can emit either depending on its configured output_field.
//
// Filename retained from the phase 3 placeholder so widget-registry
// imports stay stable.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { AttributionRenderer } from '@/components/build/model/renderers/AttributionRenderer';
import { RichModelWidget } from './shared/RichModelWidget';

const AttributionPreviewWidget: NodeRenderer = (props) => (
  <RichModelWidget
    {...props}
    Renderer={AttributionRenderer}
    toolDisplayName="attribution"
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
