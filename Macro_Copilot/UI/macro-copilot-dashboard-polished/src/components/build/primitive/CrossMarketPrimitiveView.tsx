// ============================================================================
// CrossMarketPrimitiveView — view for ``calculate_cross_market_spread_tool``.
// ----------------------------------------------------------------------------
// Cross-market spread of two sovereign curves at a single tenor.  Reshell of
// the legacy CrossMarketView onto PrimitiveCanvasShell (phase R2).
// ============================================================================

import type { CrossMarketSpreadOutput } from '@/types/rates';
import { PrimitiveChart, type PrimitiveChartPoint } from './PrimitiveChart';
import {
  PrimitiveZScoreChart,
  type PrimitiveZScorePoint,
} from './PrimitiveZScoreChart';
import {
  PrimitiveMetrics,
  formatSigned,
  toneForChange,
  toneForZScore,
  type MetricItem,
} from './PrimitiveMetrics';
import { PrimitiveCanvasShell, ToolNameChip } from './PrimitiveCanvasShell';

type Props = {
  payload: CrossMarketSpreadOutput;
};

export function CrossMarketPrimitiveView({ payload }: Props) {
  const { current_metrics: cm, time_series } = payload;

  const chartPoints: PrimitiveChartPoint[] = time_series.map((row) => ({
    date: row.date,
    value: row.spread_bps,
    z_score: row.z_score ?? null,
  }));

  const zPoints: PrimitiveZScorePoint[] = time_series.map((row) => ({
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
      value: cm.current_z_score?.toFixed(2) ?? '—',
      tone: toneForZScore(cm.current_z_score),
      subtext: `${cm.rolling_window_days}d`,
    },
    {
      label: '252d %ile',
      value:
        cm.percentile_252d != null ? `${cm.percentile_252d.toFixed(0)}` : '—',
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
      label: `${cm.curve_family_1} ${cm.tenor}`,
      value: cm.curve_family_1_yield?.toFixed(3) ?? '—',
      unit: '%',
    },
    {
      label: `${cm.curve_family_2} ${cm.tenor}`,
      value: cm.curve_family_2_yield?.toFixed(3) ?? '—',
      unit: '%',
    },
  ];

  return (
    <PrimitiveCanvasShell
      kicker="Primitive · Cross-Market Spread"
      title={`${cm.spread_label} · ${cm.tenor}`}
      subtitle="Same-tenor spread between two sovereign curves."
      asOfDate={cm.as_of_date ?? null}
      meta={<ToolNameChip tool="calculate_cross_market_spread_tool" />}
      methodology={
        <p>
          <span className="font-medium text-fg-primary">
            Spread = Curve₁ yield − Curve₂ yield
          </span>{' '}
          at the chosen tenor, in basis points. Rolling z-score uses a{' '}
          <span className="font-mono">{cm.rolling_window_days}-day</span>{' '}
          window.  Source: Bloomberg mid-yield, NY-Fed trading-day calendar.
        </p>
      }
    >
      <section className="research-card overflow-hidden">
        <div className="px-5 pt-4 pb-1">
          <div className="kicker text-fg-muted">
            {cm.spread_label} · {cm.tenor}
          </div>
        </div>
        <div className="px-2 pt-2">
          <PrimitiveChart
            data={chartPoints}
            unit="bps"
            valueDecimals={1}
            tone="blue"
            height={320}
            seriesName={cm.spread_label}
          />
        </div>
        <div className="border-t border-line-subtle px-2 pt-1 pb-2">
          <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-[0.06em] text-fg-faint">
            Rolling z-score · {cm.rolling_window_days}d
          </div>
          <PrimitiveZScoreChart data={zPoints} height={140} />
        </div>
      </section>

      <section className="research-card px-5 py-4">
        <PrimitiveMetrics items={items} title="Snapshot" desktopCols={5} />
      </section>
    </PrimitiveCanvasShell>
  );
}
