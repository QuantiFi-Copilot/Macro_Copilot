// ============================================================================
// YieldView
// ----------------------------------------------------------------------------
// /detail/yield: a single point on the curve.  No time_series in the payload
// (the metrics-only schema mirrors the original tool).  We render the metric
// grid with the headline yield emphasised and offer a clear note that the
// chart will follow once the backend returns the series.
// ============================================================================

import type { YieldLevelOutput } from '@/types/rates';
import {
  WorkspaceMetrics,
  formatSigned,
  toneForChange,
  toneForZScore,
  type MetricItem,
} from '../WorkspaceMetrics';

type YieldViewProps = {
  payload: YieldLevelOutput;
};

export function YieldView({ payload }: YieldViewProps) {
  const { current_metrics: cm } = payload;

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
      value: cm.percentile_252d != null ? `${cm.percentile_252d.toFixed(0)}` : '—',
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
    <div className="flex flex-col gap-6 px-6 py-5">
      <section className="card px-5 py-5">
        <div className="kicker mb-3">{cm.curve_family} · {cm.tenor}</div>
        <WorkspaceMetrics items={items} desktopCols={5} />
      </section>

      <section className="card flex items-center justify-center px-6 py-10 text-[11.5px] text-fg-muted">
        Time-series chart will appear here once the yield-level detail endpoint
        returns history (current schema is metrics-only).
      </section>
    </div>
  );
}
