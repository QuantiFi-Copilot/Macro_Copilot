// ============================================================================
// widgets/index.ts — initialise the build-time renderer registry.
// ----------------------------------------------------------------------------
// Importing this barrel forces every widget module to run, which in
// turn calls ``registerArtifactRenderer`` / ``registerToolRenderer`` /
// ``registerFallbackRenderer`` at module-eval time.  Consumers
// (BuildShell, NodeWidgetCard) import from here before they call
// ``resolveNodeRenderer`` so the registry is guaranteed-populated.
//
// The order of imports doesn't matter for correctness (the registry
// is keyed by artifact type) but DOES matter for "which file did
// I register last" diagnostics; we put FallbackWidget last so a
// future debug log can see that the fallback was registered after
// the per-type entries (which is the expected sequence).
//
// Per-tool overrides
// ------------------
// Stage 4b — per-tool preview widgets that used to self-register from
// ``./PcaPreviewWidget`` / ``./RollingRegressionPreviewWidget`` /
// ``./AttributionPreviewWidget`` / ``./HalfLifePreviewWidget`` /
// ``./BetaAdjustedSpreadPreviewWidget`` are now owned by their
// modules under ``src/modules/primitives/<tool_name>/surfaces/PreviewWidget.tsx``.
// This barrel walks ``ALL_PRIMITIVE_MODULES`` and registers every
// module whose ``surfaces.preview`` is populated.  Loaded AFTER the
// per-type generics so per-tool entries unambiguously win.
//
// Why the walker rather than per-file imports?  FP12 (page-shell
// minimality) forbids files under ``src/components/{build,library,
// monitor,ask,layout}`` from importing ``@/modules/primitives/<name>/``
// directly.  ``ALL_PRIMITIVE_MODULES`` from ``@/modules`` is on the
// loader's allowed-import list, so the walker is FP12-compliant.
// ============================================================================

import './SeriesWidget';
import './SeriesSetWidget';
import './EventSetWidget';
import './PanelWidget';
import './WindowedPanelWidget';
import './TradeSetWidget';
// PR-11B — re-enabled per v2.0 (ADR 0016 / ART4 / ART5).  Backend
// admitted ``ScalarMetric`` as a closed-family artifact for statistical
// operators (correlation, covariance, cointegration, ...).  The widget
// self-registers via ``registerArtifactRenderer('ScalarMetric', ...)``
// at module load.
import './ScalarMetricWidget';

import './FallbackWidget';

// Module-driven per-tool preview registrations (Stage 4b).
import { ALL_PRIMITIVE_MODULES } from '@/modules';
import {
  registerToolRenderer,
  type NodeRenderer,
} from '@/components/build/lib/nodeRendererRegistry';
import { StandardPreviewWidget } from './shared/StandardPreviewWidget';

// Stage 4d — every module's preview registers under ``Series`` by
// default.  Modules that need additional artifact-type registrations
// (today only attribution — Series AND Panel) declare them via the
// module spec's ``previewArtifactTypes`` field, eliminating the
// hand-authored DUAL_ARTIFACT_TYPE_PREVIEW_TOOLS set this file used
// to carry.
const DEFAULT_PREVIEW_ARTIFACT_TYPE = 'Series';

for (const m of ALL_PRIMITIVE_MODULES) {
  const Preview = m.surfaces?.preview as NodeRenderer | undefined;
  if (!Preview) continue;
  registerToolRenderer(
    { artifactType: DEFAULT_PREVIEW_ARTIFACT_TYPE, toolName: m.toolName },
    Preview,
  );
  for (const extra of m.previewArtifactTypes ?? []) {
    if (extra === DEFAULT_PREVIEW_ARTIFACT_TYPE) continue;
    registerToolRenderer(
      { artifactType: extra, toolName: m.toolName },
      Preview,
    );
  }
}

// Stage 4e — standard dual-view tools (buildExtended + buildCompact, no
// bespoke ``preview``) get the SHARED ``StandardPreviewWidget`` as their
// persisted-slug node body, so the open-DAG ``/workspace/:slug`` page
// renders them as a desk card (deterministic, read-only by hash) instead
// of the generic per-artifact-type stats strip.  One shared renderer,
// zero per-tool files.  Operators (no owning module) keep the per-type
// generic widget; rich-models keep their bespoke ``preview`` (the guard
// below skips any module that already declared one above, so the per-tool
// entries from the loop above are never overwritten).
for (const m of ALL_PRIMITIVE_MODULES) {
  if (m.surfaces?.preview) continue;
  if (!m.surfaces?.buildCompact) continue;
  registerToolRenderer(
    { artifactType: DEFAULT_PREVIEW_ARTIFACT_TYPE, toolName: m.toolName },
    StandardPreviewWidget as NodeRenderer,
  );
  for (const extra of m.previewArtifactTypes ?? []) {
    if (extra === DEFAULT_PREVIEW_ARTIFACT_TYPE) continue;
    registerToolRenderer(
      { artifactType: extra, toolName: m.toolName },
      StandardPreviewWidget as NodeRenderer,
    );
  }
}

export { SeriesWidget } from './SeriesWidget';
export { SeriesSetWidget } from './SeriesSetWidget';
export { EventSetWidget } from './EventSetWidget';
export { PanelWidget } from './PanelWidget';
export { WindowedPanelWidget } from './WindowedPanelWidget';
export { ScalarMetricWidget } from './ScalarMetricWidget';
export { TradeSetWidget } from './TradeSetWidget';
export { FallbackWidget } from './FallbackWidget';
