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
// PR4 — ``ScalarMetricWidget`` registration is deliberately dropped:
// the backend's ``ArtifactTypeLiteral`` is closed at six families
// and ``ScalarMetric`` is a deferred future addition (see
// ``shared/operators/summarize_trades/config.yaml`` planned_extensions).
// Scalar-shaped outputs ship as ``Panel`` with one row today; the
// PR4 ``PanelWidget`` handles them.  See ``ScalarMetricWidget.tsx``
// header for the re-enablement checklist.

import './FallbackWidget';

// Module-driven per-tool preview registrations (Stage 4b).
import { ALL_PRIMITIVE_MODULES } from '@/modules';
import {
  registerToolRenderer,
  type NodeRenderer,
} from '@/components/build/lib/nodeRendererRegistry';

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

export { SeriesWidget } from './SeriesWidget';
export { SeriesSetWidget } from './SeriesSetWidget';
export { EventSetWidget } from './EventSetWidget';
export { PanelWidget } from './PanelWidget';
export { WindowedPanelWidget } from './WindowedPanelWidget';
export { ScalarMetricWidget } from './ScalarMetricWidget';
export { TradeSetWidget } from './TradeSetWidget';
export { FallbackWidget } from './FallbackWidget';
