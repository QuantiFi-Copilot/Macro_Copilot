// ============================================================================
// ScalarMetricWidget — payload-backed renderer for ScalarMetric artifacts.
// ----------------------------------------------------------------------------
// PR-11B / v2.0 (ART4 / ART5 / ADR 0016) — admits a real renderer for the
// closed-family ScalarMetric shape.  Backend wire shape:
//
//   {
//     artifact_type: "ScalarMetric",
//     metadata: { metric_key: string, units: string, lineage: {...} },
//     payload:  { value: number (finite — ART11 forbids ±Inf/NaN) }
//   }
//
// Rendered as a labelled big-number metric:
//
//   ┌─────────────────────────────────┐
//   │ METRIC_KEY · UNITS              │
//   │  0.62                           │   (large number, formatted per units)
//   │ scalar statistic                │   (small caption)
//   └─────────────────────────────────┘
//
// Sizes gracefully across the NodeWidgetCard envelope: smaller display
// than Series/Panel (no sparkline, no table) — sufficient for both the
// terminal slot AND an intermediate-stage card in the GenericResultsDashboard
// intermediate-stages toggle path.
//
// Formatting
// ----------
// Uses ``formatNumberWithUnits`` from the shared formatter so the
// per-unit display rule stays consistent with Series/Panel widgets:
//   - RATIO (correlation, etc.)   → 3 significant digits, no suffix
//   - BPS                          → 1 decimal, " bps" suffix
//   - PERCENT                      → 2 decimals, "%" suffix
//   - Z_SCORE                      → 2 decimals, no suffix
//   - other                        → defaults from formatNumber
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { PayloadShell } from './shared/PayloadShell';
import {
  formatNumberWithUnits,
  MISSING_VALUE_DASH,
  type ScalarMetricPayloadEnvelope,
} from './shared/artifactFormat';

const ScalarMetricWidget: NodeRenderer = ({ node, artifact }) => {
  return (
    <PayloadShell<ScalarMetricPayloadEnvelope>
      artifactHash={node.artifact_hash ?? artifact.hash}
      expectedType="ScalarMetric"
      displayName="Scalar metric"
      isEmpty={(p) =>
        p.payload?.value === null ||
        p.payload?.value === undefined ||
        !Number.isFinite(p.payload.value)
      }
      emptyMessage="The scalar metric persisted but its value is not finite."
    >
      {(payload) => <ScalarMetricBody payload={payload} />}
    </PayloadShell>
  );
};

function ScalarMetricBody({
  payload,
}: {
  payload: ScalarMetricPayloadEnvelope;
}) {
  const metricKey = payload.metadata.metric_key;
  const units = payload.metadata.units;
  const value = payload.payload.value;
  const valueText = Number.isFinite(value)
    ? formatNumberWithUnits(value, units)
    : MISSING_VALUE_DASH;

  return (
    <div className="flex min-h-0 flex-1 flex-col px-5 pt-3 pb-4">
      <div className="kicker text-fg-muted">
        {metricKey}
        {units && (
          <span className="ml-2 normal-case tracking-normal text-fg-faint">
            {units}
          </span>
        )}
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="font-serif-display text-[24px] font-light leading-none text-fg-primary">
          {valueText}
        </span>
      </div>
      <p className="mt-3 text-[10.5px] leading-[1.5] text-fg-faint">
        Scalar statistic — a single finite value emitted by an
        L3 statistical operator (e.g. correlation, covariance,
        cointegration).  Lineage chain available via the artifact
        footer.
      </p>
    </div>
  );
}

registerArtifactRenderer('ScalarMetric', ScalarMetricWidget);
export { ScalarMetricWidget };
