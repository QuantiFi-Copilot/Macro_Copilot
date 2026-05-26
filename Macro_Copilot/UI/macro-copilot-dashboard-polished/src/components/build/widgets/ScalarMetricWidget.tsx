// ============================================================================
// ScalarMetricWidget — REGISTRATION DROPPED IN PR4.
// ----------------------------------------------------------------------------
// Background
// ----------
// The frontend used to register a ``ScalarMetricWidget`` against the
// artifact_type string ``'ScalarMetric'``.  No backend artifact uses
// that type: ``state.schemas.ArtifactTypeLiteral`` is closed at six
// names today — ``Series | SeriesSet | EventSet | Panel | WindowedPanel
// | TradeSet`` — and the substrate-canonical ``ScalarMetric`` artifact
// is explicitly deferred per
// ``shared/operators/summarize_trades/config.yaml``'s
// ``planned_extensions``:
//
//   "ScalarMetric closed-family artifact for the metric values
//    (vs the current Panel-with-one-row encoding)"
//
// Today scalar-shaped outputs ship as ``Panel`` with one row; the
// PR4 ``PanelWidget`` renders them as a labelled metric strip and
// that path is the canonical surface for scalar values.
//
// PR4 outcome
// -----------
// We KEEP this file (importing the symbol elsewhere shouldn't crash
// the bundle), but we DROP the registry-renderer call so no artifact
// type is routed here.  If a future backend ships a real scalar
// artifact, the work is:
//
//   1. Add the new name to ``ArtifactTypeLiteral`` and the frontend
//      ``ArtifactType`` union.
//   2. Add a real metadata + payload shape to ``types/artifacts.ts``.
//   3. Re-introduce the registry-renderer call here with an
//      implementation that reads the payload (mirror what
//      EventSetWidget / PanelWidget do).
//
// Until then, the ``FallbackWidget`` handles any hypothetical orphan
// artifact whose type isn't in the closed family.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';

const ScalarMetricWidget: NodeRenderer = () => null;

// NB: NO ``registerArtifactRenderer`` call.  Re-enable when the
// backend ships the type — see file header for the checklist.

export { ScalarMetricWidget };
