// ============================================================================
// PcaPreviewWidget — per-tool persisted-artifact card for PCA.
// ----------------------------------------------------------------------------
// PR1 (new plan) — pivots from the legacy live-output renderer
// (``PcaLoadingsRenderer``) to the payload-shape-correct
// ``RichModelWidget``.  The persisted PCA artifact is a single
// factor-score Series; loadings + variance + current factor levels
// only exist on the live-run *Output dict (see
// ``model/ModelWorkspacePage`` for the live-run path).  The widget
// now renders the persisted Series + an honest "what's not in this
// snapshot" callout via the per-tool adapter.
//
// The filename + per-tool registry key
// (``Series:calculate_pca_yield_curve_tool``) stay stable so existing
// imports + the resolver lookup don't drift.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { RichModelWidget } from './shared/RichModelWidget';

const PcaPreviewWidget: NodeRenderer = (props) => (
  <RichModelWidget {...props} toolName="calculate_pca_yield_curve_tool" />
);

registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_pca_yield_curve_tool' },
  PcaPreviewWidget,
);

export { PcaPreviewWidget };
