// ============================================================================
// RollingRegressionPreviewWidget — per-tool renderer for rolling regression.
// ----------------------------------------------------------------------------
// Phase R3 replaces the thin "preview" widget with the rich
// ``RollingRegressionRenderer`` (rolling β / α / R² / residual stack
// from the legacy model-workspace).  The bridge re-runs the tool via
// ``RichModelWidget`` so the full output is in hand even when the
// persisted artifact summary only carries a sparkline-grade preview.
//
// Filename retained from the phase 3 placeholder so widget-registry
// imports stay stable.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { RollingRegressionRenderer } from '@/components/build/model/renderers/RollingRegressionRenderer';
import { RichModelWidget } from './shared/RichModelWidget';

const RollingRegressionPreviewWidget: NodeRenderer = (props) => (
  <RichModelWidget
    {...props}
    Renderer={RollingRegressionRenderer}
    toolDisplayName="rolling regression"
  />
);

registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_rolling_regression_tool' },
  RollingRegressionPreviewWidget,
);

export { RollingRegressionPreviewWidget };
