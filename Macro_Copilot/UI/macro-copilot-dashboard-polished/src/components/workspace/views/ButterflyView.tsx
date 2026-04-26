// ============================================================================
// ButterflyView
// ----------------------------------------------------------------------------
// /detail/butterfly: 3-leg curvature trade.  Same chart shape as spread but
// the metric block exposes wing values + 3 leg yields.
// ============================================================================

import type { ButterflyOutput } from '@/types/rates';
import { WorkspaceChart, type WorkspaceChartPoint } from '../WorkspaceChart';
import { WorkspaceZScoreChart, type WorkspaceZScorePoint } from '../WorkspaceZScoreChart';
import {
  WorkspaceMetrics,
  formatSigned,
  toneForChange,
  toneForZScore,
  type MetricItem,
} from '../WorkspaceMetrics';

type ButterflyViewProps = {
  payload: ButterflyOutput;
};

export function ButterflyView({ payload }: ButterflyViewProps) {
  const { current_metrics: cm, time_series } = payload;

  const chartPoints: WorkspaceChartPoint[] = time_series.map((row) => ({
    date: row.date,
    value: row.butterfly_bps,
    z_score: row.z_score ?? null,
  }));

  const zPoints: WorkspaceZScorePoint[] = time_series.map((row) => ({
    date: row.date,
    z_score: row.z_score ?? null,
  }));

  const items: MetricItem[] = [
    {
      label: 'Butterfly',
      value: cm.current_butterfly_bps?.toFixed(1) ?? '—',
      unit: 'bps',
      emphasis: true,
    },
    {
      label: 'Daily Δ',
      value: formatSigned(cm.daily_change_bps, 1),
      unit: 'bps',
      tone: toneForChange(cm.daily_change_bps),
    },
    {
      label: 'Z-score',
      value: cm.current_z_score?.toFixed(2) ?? '—',
      tone: toneForZScore(cm.current_z_score),
      subtext: `${cm.rolling_window_days}d`,
    },
    {
      label: '252d %ile',
      value: cm.percentile_252d != null ? `${cm.percentile_252d.toFixed(0)}` : '—',
      unit: '%',
    },
    {
      label: '252d high',
      value: cm.high_252d_bps?.toFixed(1) ?? '—',
      unit: 'bps',
    },
    {
      label: '252d low',
      value: cm.low_252d_bps?.toFixed(1) ?? '—',
      unit: 'bps',
    },
    {
      label: 'Wing short',
      value: cm.wing_short_bps?.toFixed(1) ?? '—',
      unit: 'bps',
    },
    {
      label: 'Wing long',
      value: cm.wing_long_bps?.toFixed(1) ?? '—',
      unit: 'bps',
    },
    {
      label: 'Short yld',
      value: cm.short_tenor_yield?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: 'Belly yld',
      value: cm.belly_tenor_yield?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: 'Long yld',
      value: cm.long_tenor_yield?.toFixed(3) ?? '—',
      unit: '%',
    },
  ];

  return (
    <div className="flex flex-col gap-6 px-6 py-5">
      <section className="card overflow-hidden">
        <div className="px-5 pt-4 pb-1">
          <div className="kicker">
            {cm.curve_family} · {cm.butterfly_label}
          </div>
        </div>
        <div className="px-2 pt-2">
          <WorkspaceChart
            data={chartPoints}
            unit="bps"
            valueDecimals={1}
            tone="amber"
            height={320}
            seriesName={cm.butterfly_label}
          />
        </div>
        <div className="border-t border-line-subtle px-2 pt-1 pb-2">
          <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-[0.06em] text-fg-faint">
            Rolling z-score
          </div>
          <WorkspaceZScoreChart data={zPoints} height={140} />
        </div>
      </section>

      <section className="card px-5 py-4">
        <WorkspaceMetrics items={items} title="Snapshot" desktopCols={6} />
      </section>
    </div>
  );
}
