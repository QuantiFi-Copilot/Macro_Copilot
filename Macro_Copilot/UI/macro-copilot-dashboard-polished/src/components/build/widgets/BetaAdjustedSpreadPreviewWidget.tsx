// ============================================================================
// BetaAdjustedSpreadPreviewWidget — per-tool persisted-artifact card.
// ----------------------------------------------------------------------------
// PR1 (new plan) — beta-adjusted spread's output_class emits three
// rolling time-series (beta / residual / residual_z_score) plus a
// snapshot ``current_metrics`` block.  The substrate persists one
// of those three time-series as a Series artifact based on
// ``output_field``; the snapshot scalars only live on the live
// *Output dict.  The widget renders the saved Series + an honest
// "what's not in this snapshot" callout via the per-tool adapter.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { RichModelWidget } from './shared/RichModelWidget';

const BetaAdjustedSpreadPreviewWidget: NodeRenderer = (props) => (
  <RichModelWidget
    {...props}
    toolName="calculate_beta_adjusted_spread_tool"
  />
);

registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_beta_adjusted_spread_tool' },
  BetaAdjustedSpreadPreviewWidget,
);

export { BetaAdjustedSpreadPreviewWidget };
