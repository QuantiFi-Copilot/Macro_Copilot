// ============================================================================
// __smoke_test_tool/surfaces/BuildCompact.tsx
// ----------------------------------------------------------------------------
// Stage 5 acceptance test fixture (G-3.5: dual-view contract).  The
// compact half of the dual-view pair — mounted as a node body inside
// multi-tool DAG visualizations.  Exists so the fixture satisfies the
// loader-level invariant "custom_build_surface ⇒ buildExtended AND
// buildCompact present".
// ============================================================================

import type { ModuleSurfaceBaseProps } from '@/modules/types';

function SmokeBuildCompact({ toolName }: ModuleSurfaceBaseProps) {
  return (
    <div data-smoke-surface="build-compact">
      <h3>SMOKE: compact build surface for {toolName}</h3>
    </div>
  );
}

export default SmokeBuildCompact;
