// ============================================================================
// __smoke_test_tool/surfaces/PreviewWidget.tsx — Stage 5 fixture.
// ============================================================================

import type { NodeRenderProps } from '@/components/build/lib/nodeRendererRegistry';

function SmokePreviewWidget(_props: NodeRenderProps) {
  return (
    <div data-smoke-surface="preview">
      <p>SMOKE: persisted-artifact preview</p>
    </div>
  );
}

export default SmokePreviewWidget;
