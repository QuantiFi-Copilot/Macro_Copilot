// ============================================================================
// widgets/index.ts — initialise the build-time renderer registry.
// ----------------------------------------------------------------------------
// Importing this barrel forces every widget module to run, which in
// turn calls ``registerArtifactRenderer`` / ``registerToolRenderer`` /
// ``registerFallbackRenderer`` at module-eval time.  Consumers
// (BuildShell, NodeWidgetCard) import from here before they call
// ``resolveNodeRenderer`` so the registry is guaranteed-populated.
//
// Ordering matters only for diagnostics (the registry is keyed) but
// we put generic per-type renderers FIRST, then per-tool overrides,
// then the fallback last.  A registration trace at debug-time then
// reads as "type defaults → specialisations → fallback safety net".
// ============================================================================

// Per-artifact-type generic renderers.
import './SeriesWidget';
import './SeriesSetWidget';
import './EventSetWidget';
import './PanelWidget';
import './WindowedPanelWidget';
import './ScalarMetricWidget';
import './TradeSetWidget';

// Per-tool specialisations.  PR C — visual fidelity pass.  Each
// primitive that has a meaningfully different reading model than the
// generic ``SeriesWidget`` gets its own renderer; the registry resolves
// them ahead of the per-type default in ``resolveNodeRenderer``.
import './ZScoreWidget';
import './CurveSpreadWidget';
import './RollingRegressionWidget';
import './PCAWidget';

// Fallback last — any artifact type we haven't registered for falls
// through cleanly rather than blowing up the render.
import './FallbackWidget';

export { SeriesWidget } from './SeriesWidget';
export { SeriesSetWidget } from './SeriesSetWidget';
export { EventSetWidget } from './EventSetWidget';
export { PanelWidget } from './PanelWidget';
export { WindowedPanelWidget } from './WindowedPanelWidget';
export { ScalarMetricWidget } from './ScalarMetricWidget';
export { TradeSetWidget } from './TradeSetWidget';
export { ZScoreWidget } from './ZScoreWidget';
export { CurveSpreadWidget } from './CurveSpreadWidget';
export { RollingRegressionWidget } from './RollingRegressionWidget';
export { PCAWidget } from './PCAWidget';
export { FallbackWidget } from './FallbackWidget';
