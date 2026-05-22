// ============================================================================
// HalfLifePreviewWidget — per-tool persisted-artifact card for half-life.
// ----------------------------------------------------------------------------
// PR1 (new plan) — half-life's output_class is a pure snapshot
// (current_metrics only — no time_series_* field).  Today the
// persisted-Series path doesn't exist end-to-end (the executor's
// Series bridge would have nothing to lift).  Registering this
// per-tool widget gives a persisted half-life Series — should one
// ever be lifted — an honest "snapshot view not persistable today"
// callout via the per-tool adapter, in line with how attribution
// is handled.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { RichModelWidget } from './shared/RichModelWidget';

const HalfLifePreviewWidget: NodeRenderer = (props) => (
  <RichModelWidget {...props} toolName="calculate_half_life_tool" />
);

registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_half_life_tool' },
  HalfLifePreviewWidget,
);

export { HalfLifePreviewWidget };
