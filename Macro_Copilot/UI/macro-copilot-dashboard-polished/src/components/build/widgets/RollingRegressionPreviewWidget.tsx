// ============================================================================
// RollingRegressionPreviewWidget — per-tool persisted-artifact card.
// ----------------------------------------------------------------------------
// PR1 (new plan) — same pivot as ``PcaPreviewWidget``: the persisted
// artifact is a single rolling time-series (β / α / R² / residual /
// condition-flag) — whichever the workspace lifted via
// ``output_field``.  Latest-fit snapshot + peer time-series live
// only on the live *Output dict (see ``model/ModelWorkspacePage``).
//
// Filename + per-tool registry key
// (``Series:calculate_rolling_regression_tool``) stay stable.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { RichModelWidget } from './shared/RichModelWidget';

const RollingRegressionPreviewWidget: NodeRenderer = (props) => (
  <RichModelWidget
    {...props}
    toolName="calculate_rolling_regression_tool"
  />
);

registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_rolling_regression_tool' },
  RollingRegressionPreviewWidget,
);

export { RollingRegressionPreviewWidget };
