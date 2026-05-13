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
// Tools that emit a generic artifact type (e.g. Series) but deserve
// a specialised reading (PCA factor, rolling regression, attribution)
// register under the per-tool registry.  Loaded AFTER the per-type
// generics so a per-tool entry always wins when both apply.
// ============================================================================

import './SeriesWidget';
import './SeriesSetWidget';
import './EventSetWidget';
import './PanelWidget';
import './WindowedPanelWidget';
import './ScalarMetricWidget';
import './TradeSetWidget';

// Per-tool specialisations — registered after the per-type generics
// so they unambiguously win the lookup priority.
import './PcaPreviewWidget';
import './RollingRegressionPreviewWidget';
import './AttributionPreviewWidget';

import './FallbackWidget';

export { SeriesWidget } from './SeriesWidget';
export { SeriesSetWidget } from './SeriesSetWidget';
export { EventSetWidget } from './EventSetWidget';
export { PanelWidget } from './PanelWidget';
export { WindowedPanelWidget } from './WindowedPanelWidget';
export { ScalarMetricWidget } from './ScalarMetricWidget';
export { TradeSetWidget } from './TradeSetWidget';
export { PcaPreviewWidget } from './PcaPreviewWidget';
export { RollingRegressionPreviewWidget } from './RollingRegressionPreviewWidget';
export { AttributionPreviewWidget } from './AttributionPreviewWidget';
export { FallbackWidget } from './FallbackWidget';
