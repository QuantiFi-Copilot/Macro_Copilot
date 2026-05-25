// ============================================================================
// YieldPrimitiveView — view for ``get_yield_levels_tool``.
// ----------------------------------------------------------------------------
// Single point on the curve.  Reshell of the legacy YieldView onto
// PrimitiveCanvasShell (phase R2).
//
// PR-E — wire the time-series chart.  The backend YieldLevelOutput has
// shipped a canonical ``time_series`` (rows = [{date, value}], units =
// percent) since the legacy-TimeSeries cleanup, but the frontend was
// still rendering a "Time-series chart will appear here once the
// yield-level detail endpoint returns history" literal placeholder.
// This view now consumes the field if present and renders the same
// PrimitiveChart treatment the curve-spread and cross-market views
// already use.  Optional-typed for defensive resilience against stale
// payloads that pre-date the backend cleanup — falls back to a "no
// data in window" message via PrimitiveChart's own empty state.
// ============================================================================

import type { YieldLevelOutput } from '@/types/rates';
import { PrimitiveChart, type PrimitiveChartPoint } from '@/components/build/primitive/PrimitiveChart';
import {
  PrimitiveMetrics,
  formatSigned,
  toneForChange,
  toneForZScore,
  type MetricItem,
} from '@/components/build/primitive/PrimitiveMetrics';
import { PrimitiveCanvasShell, ToolNameChip } from '@/components/build/primitive/PrimitiveCanvasShell';

type Props = {
  payload: YieldLevelOutput;
};

function YieldPrimitiveView({ payload }: Props) {
  const { current_metrics: cm, time_series } = payload;

  // PR-E — map the canonical TimeSeries rows onto PrimitiveChart's
  // point shape.  Defensive on three axes:
  //   1. ``time_series`` is optional in the TS type for stale-payload
  //      resilience — fall back to an empty array so the chart's own
  //      "No data in window" state renders cleanly.
  //   2. ``row.value`` is ``number | null`` (the backend emits null
  //      for warmup / gap days); filter those out so the area chart
  //      doesn't draw to NaN.
  //   3. We do NOT propagate a per-row z_score (the yield-level
  //      backend emits a SINGLE rolling z-score on ``current_metrics``,
  //      not a per-day series) — leaving ``z_score`` undefined keeps
  //      the chart tooltip from rendering the z row.
  const chartPoints: PrimitiveChartPoint[] = (time_series?.rows ?? [])
    .filter((row): row is { date: string; value: number } => row.value != null)
    .map((row) => ({ date: row.date, value: row.value }));

  const items: MetricItem[] = [
    {
      label: 'Yield',
      value: cm.current_yield_pct?.toFixed(3) ?? '—',
      unit: '%',
      emphasis: true,
    },
    {
      label: 'Daily Δ',
      value: formatSigned(cm.daily_change_bps, 1),
      unit: 'bps',
      tone: toneForChange(cm.daily_change_bps),
    },
    {
      label: 'Weekly Δ',
      value: formatSigned(cm.weekly_change_bps, 1),
      unit: 'bps',
      tone: toneForChange(cm.weekly_change_bps),
    },
    {
      label: 'Monthly Δ',
      value: formatSigned(cm.monthly_change_bps, 1),
      unit: 'bps',
      tone: toneForChange(cm.monthly_change_bps),
    },
    {
      label: 'Z-score',
      value: cm.z_score?.toFixed(2) ?? '—',
      tone: toneForZScore(cm.z_score),
    },
    {
      label: '252d %ile',
      value:
        cm.percentile_252d != null ? `${cm.percentile_252d.toFixed(0)}` : '—',
      unit: '%',
    },
    {
      label: '252d high',
      value: cm.high_252d_pct?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: '252d low',
      value: cm.low_252d_pct?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: 'Observations',
      value: cm.observation_count?.toString() ?? '—',
    },
  ];

  return (
    <PrimitiveCanvasShell
      kicker="Primitive · Yield Level"
      title={`${cm.curve_family} ${cm.tenor}`}
      subtitle="Single-point yield level with rolling z-score."
      asOfDate={cm.as_of_date ?? null}
      meta={<ToolNameChip tool="get_yield_levels_tool" />}
      methodology={
        <p>
          Mid-yield at the chosen tenor (Bloomberg).  Daily / weekly /
          monthly changes are calendar-day differences; the 252-day
          percentile is computed over the trailing trading-day window.
          The historical series below is cleaned and forward-filled
          over the display window — its latest row matches the
          snapshot's current yield byte-for-byte.
        </p>
      }
    >
      <section className="research-card px-5 py-5">
        <PrimitiveMetrics items={items} title="Snapshot" desktopCols={5} />
      </section>

      <section className="research-card overflow-hidden">
        <div className="px-5 pt-4 pb-1">
          <div className="kicker text-fg-muted">
            {cm.curve_family} {cm.tenor} · yield
          </div>
        </div>
        <div className="px-2 pt-2 pb-2">
          <PrimitiveChart
            data={chartPoints}
            unit="%"
            valueDecimals={3}
            tone="blue"
            height={320}
            seriesName={`${cm.curve_family} ${cm.tenor}`}
          />
        </div>
      </section>
    </PrimitiveCanvasShell>
  );
}

export default YieldPrimitiveView;
