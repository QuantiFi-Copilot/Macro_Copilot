// ============================================================================
// YieldPrimitiveView — view for ``get_yield_levels_tool``.
// ----------------------------------------------------------------------------
// Single point on the curve; current schema is metrics-only (no
// time_series).  Reshell of the legacy YieldView onto PrimitiveCanvasShell
// (phase R2).  When the backend ships a yield-level time series, swap the
// "chart will appear here" placeholder for a PrimitiveChart.
// ============================================================================

import type { YieldLevelOutput } from '@/types/rates';
import {
  PrimitiveMetrics,
  formatSigned,
  toneForChange,
  toneForZScore,
  type MetricItem,
} from './PrimitiveMetrics';
import { PrimitiveCanvasShell, ToolNameChip } from './PrimitiveCanvasShell';

type Props = {
  payload: YieldLevelOutput;
};

export function YieldPrimitiveView({ payload }: Props) {
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
          A time-series chart will appear here once the yield-level
          detail endpoint ships history (current schema is metrics-only).
        </p>
      }
    >
      <section className="research-card px-5 py-5">
        <PrimitiveMetrics items={items} title="Snapshot" desktopCols={5} />
      </section>

      <section className="research-card flex items-center justify-center px-6 py-10 text-[11.5px] text-fg-muted">
        Time-series chart will appear here once the yield-level detail
        endpoint returns history.
      </section>
    </PrimitiveCanvasShell>
  );
}
