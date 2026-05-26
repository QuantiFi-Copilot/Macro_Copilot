// ============================================================================
// __smoke_test_tool/surfaces/BuildSurface.tsx
// ----------------------------------------------------------------------------
// Stage 5 acceptance test fixture.  This surface proves that a module
// can ship a fully custom Build canvas and BOTH ``VirtualPrimitiveCanvas``
// (the ``?context=`` route) AND ``BuildShell`` (the ``?builder=`` route)
// mount it through the module-first dispatch — without any edits to
// either of those page-shell files.
//
// The component renders a recognisable marker so the structural
// acceptance test can grep the bundled source for proof that the
// dispatcher walked to the module's shelf.
// ============================================================================

import type { BuildSurfaceProps } from '@/modules/types';

function SmokeBuildSurface({ toolName }: BuildSurfaceProps) {
  return (
    <div data-smoke-surface="build">
      <h2>SMOKE: build surface for {toolName}</h2>
    </div>
  );
}

export default SmokeBuildSurface;
