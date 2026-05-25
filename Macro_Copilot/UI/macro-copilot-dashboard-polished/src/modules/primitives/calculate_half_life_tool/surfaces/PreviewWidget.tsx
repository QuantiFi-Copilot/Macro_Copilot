// ============================================================================
// src/modules/primitives/calculate_half_life_tool/surfaces/PreviewWidget.tsx
// ----------------------------------------------------------------------------
// Stage 4b — rich-model persisted-artifact preview widget for PCA.
// Thin caller of the shared ``RichModelWidget`` with the per-tool
// ``toolName`` prop.  See ``RichModelWidget`` for the full payload-
// shape adaptation flow (persisted Series / shape mismatch /
// pure snapshot unavailable).
//
// Stage 4a/4b precedent
// ---------------------
// The pre-Stage-4b widget at ``src/components/build/widgets/PcaPreviewWidget.tsx``
// self-registered into ``nodeRendererRegistry`` via a side-effect
// ``registerToolRenderer`` call.  The side-effect registration has
// moved into the ``widgets/index.ts`` barrel which now walks
// ``ALL_PRIMITIVE_MODULES.filter(m => m.surfaces?.preview)`` — that
// keeps page-shell files free of per-module imports (FP12) and lets
// this surface file stay a pure component.
// ============================================================================

import type { NodeRenderProps } from '@/components/build/lib/nodeRendererRegistry';
import { RichModelWidget } from '@/components/build/widgets/shared/RichModelWidget';

function PreviewWidget(props: NodeRenderProps) {
  return <RichModelWidget {...props} toolName="calculate_half_life_tool" />;
}

export default PreviewWidget;
