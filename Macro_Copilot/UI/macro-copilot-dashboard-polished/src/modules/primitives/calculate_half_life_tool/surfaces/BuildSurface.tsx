// ============================================================================
// src/modules/primitives/calculate_half_life_tool/surfaces/BuildSurface.tsx
// ----------------------------------------------------------------------------
// Stage 4b — rich-model Build surface.  Thin wrapper around the shared
// ``BuilderCanvas``: when the page-shell migrates to module-driven
// mounting (Stage N+), it will resolve this component via
// ``getPrimitiveModule(toolName).surfaces.build`` and forward the
// decoded toolName + params here.  Until then the page-shell still
// mounts ``BuilderCanvas`` directly from BuildShell's ``?builder=``
// branch; this wrapper exists to satisfy FM8 (every claimed
// capability tier has a populated surface file) and to make the
// future migration mechanical.
//
// Lazy import — why
// -----------------
// ``BuilderCanvas`` transitively pulls in ``ModelWorkspacePage`` →
// ``modelRegistry``.  ``modelRegistry`` derives ``MODELS`` from
// ``ALL_PRIMITIVE_MODULES``; that derivation IS lazy on the registry
// side, but the bundler still emits a runtime-load edge from this
// file to ``BuilderCanvas`` at module-init time, which causes the
// rich-model module.ts → BuildSurface → BuilderCanvas → ...
// → modelRegistry → toolNames → @/modules cycle to trip on the
// ``toolNames`` derivation (it reads ``ALL_PRIMITIVE_MODULES`` at
// module-init).  ``React.lazy`` defers the
// ``BuilderCanvas`` import until first render, so the module-init
// graph never crosses into the model surfaces.
// ============================================================================

import { Suspense, lazy } from 'react';
import type { BuildSurfaceProps } from '@/modules/types';

const BuilderCanvas = lazy(() =>
  import('@/components/build/model/BuilderCanvas').then((m) => ({
    default: m.BuilderCanvas,
  })),
);

function BuildSurface({ toolName, params }: BuildSurfaceProps) {
  return (
    <Suspense fallback={null}>
      <BuilderCanvas toolName={toolName} initialParams={params} />
    </Suspense>
  );
}

export default BuildSurface;
