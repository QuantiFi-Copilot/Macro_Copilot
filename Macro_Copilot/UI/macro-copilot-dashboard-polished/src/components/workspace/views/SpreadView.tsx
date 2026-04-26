// ============================================================================
// SpreadView
// ----------------------------------------------------------------------------
// Demo-priority view: renders /detail/spread output for the workspace.  The
// payload exposes spread_bps + z_score time series and a current_metrics
// block; we lay them out as primary chart → z-score ribbon → metric grid.
// ============================================================================

import type { CurveSpreadOutput } from '@/types/rates';
import { WorkspaceChart, type WorkspaceChartPoint } from '../WorkspaceChart';
import { WorkspaceZScoreChart, type WorkspaceZScorePoint } from '../WorkspaceZScoreChart';
import {
  WorkspaceMetrics,
  formatSigned,
  toneForChange,
  toneForZScore,
  type MetricItem,
} from '../WorkspaceMetrics';

type SpreadViewProps = {
  payload: CurveSpreadOutput;
};

export function SpreadView({ payload }: SpreadViewProps) {
  const { current_metrics: cm, time_series } = payload;

  const chartPoints: WorkspaceChartPoint[] = time_series.map((row) => ({
    date: row.date,
    value: row.spread_bps,
    z_score: row.z_score ?? null,
  }));

  const zPoints: WorkspaceZScorePoint[] = time_series.map((row) => ({
    date: row.date,
    z_score: row.z_score ?? null,
  }));

  const items: MetricItem[] = [
    {
      label: 'Spread',
      value: cm.current_spread_bps?.toFixed(1) ?? '—',
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
      subtext: `${cm.rolling_window_days}d window`,
    },
    {
      label: `${cm.spread_label.split('-')[0]?.trim() ?? 'Short'} yld`,
      value: cm.short_tenor_yield?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: `${cm.spread_label.split('-')[1]?.trim() ?? 'Long'} yld`,
      value: cm.long_tenor_yield?.toFixed(3) ?? '—',
      unit: '%',
    },
  ];

  return (
    <div className="flex flex-col gap-6 px-6 py-5">
      <section className="card overflow-hidden">
        <div className="px-5 pt-4 pb-1">
          <div className="kicker">{cm.curve_family} · {cm.spread_label}</div>
        </div>
        <div className="px-2 pt-2">
          <WorkspaceChart
            data={chartPoints}
            unit="bps"
            valueDecimals={1}
            tone="rates"
            height={320}
            seriesName={cm.spread_label}
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
        <WorkspaceMetrics items={items} title="Snapshot" desktopCols={5} />
      </section>
    </div>
  );
}
