// ============================================================================
// widgets/index.ts — initialise the build-time renderer registry.
// ----------------------------------------------------------------------------
// Importing this barrel forces every widget module to run, which in
// turn calls ``registerArtifactRenderer`` / ``registerFallbackRenderer``
// at module-eval time.  Consumers (BuildShell, NodeWidgetCard) import
// from here before they call ``resolveNodeRenderer`` so the registry
// is guaranteed-populated.
//
// The order of imports doesn't matter for correctness (the registry
// is keyed by artifact type) but DOES matter for "which file did
// I register last" diagnostics; we put FallbackWidget last so a
// future debug log can see that the fallback was registered after
// the per-type entries (which is the expected sequence).
// ============================================================================

import './SeriesWidget';
import './SeriesSetWidget';
import './EventSetWidget';
import './PanelWidget';
import './WindowedPanelWidget';
import './ScalarMetricWidget';
import './TradeSetWidget';
import './FallbackWidget';

export { SeriesWidget } from './SeriesWidget';
export { SeriesSetWidget } from './SeriesSetWidget';
export { EventSetWidget } from './EventSetWidget';
export { PanelWidget } from './PanelWidget';
export { WindowedPanelWidget } from './WindowedPanelWidget';
export { ScalarMetricWidget } from './ScalarMetricWidget';
export { TradeSetWidget } from './TradeSetWidget';
export { FallbackWidget } from './FallbackWidget';
