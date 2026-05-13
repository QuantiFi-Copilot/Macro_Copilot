// ============================================================================
// PcaPreviewWidget — per-tool renderer for ``calculate_pca_yield_curve_tool``.
// ----------------------------------------------------------------------------
// Phase R3 replaces the original thin "preview" widget with the rich
// ``PcaLoadingsRenderer`` lifted from the legacy model-workspace.  The
// widget bridges the persisted-workspace surface to the bespoke renderer
// via ``RichModelWidget``, which re-runs the tool to fetch the full output
// dict (until the backend ships a payload-fetch endpoint).
//
// The file keeps its historical name so the registry imports + the per-
// tool key (``Series:calculate_pca_yield_curve_tool``) stay stable — only
// the body changes.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { PcaLoadingsRenderer } from '@/components/build/model/renderers/PcaLoadingsRenderer';
import { RichModelWidget } from './shared/RichModelWidget';

const PcaPreviewWidget: NodeRenderer = (props) => (
  <RichModelWidget
    {...props}
    Renderer={PcaLoadingsRenderer}
    toolDisplayName="PCA"
  />
);

registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_pca_yield_curve_tool' },
  PcaPreviewWidget,
);

export { PcaPreviewWidget };
