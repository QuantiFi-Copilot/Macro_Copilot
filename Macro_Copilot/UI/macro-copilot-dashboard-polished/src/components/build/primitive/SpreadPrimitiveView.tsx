// ============================================================================
// SpreadPrimitiveView — typed primitive view for ``calculate_curve_spread_tool``.
// ----------------------------------------------------------------------------
// Restored from legacy SpreadView and reshelled onto PrimitiveCanvasShell so
// the Ask→Build handoff for "what is the 2s10s spread" lands on a polished,
// detailed Build view (mockup B level) rather than the empty shell.
// ============================================================================

import type { CurveSpreadOutput } from '@/types/rates';
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
  payload: CurveSpreadOutput;
};

export function SpreadPrimitiveView({ payload }: Props) {
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
    {
      label: 'Observations',
      value: time_series.length.toString(),
    },
  ];

  return (
    <PrimitiveCanvasShell
      kicker="Primitive · Curve Spread"
      title={`${cm.curve_family} ${cm.spread_label}`}
      subtitle="Curve spread between two tenors of the same sovereign curve."
      asOfDate={cm.as_of_date ?? null}
      meta={<ToolNameChip tool="calculate_curve_spread_tool" />}
      methodology={
        <>
          <p>
            <span className="font-medium text-fg-primary">
              Spread = Long-tenor yield − Short-tenor yield
            </span>
            , reported in basis points.  The rolling z-score uses a{' '}
            <span className="font-mono">{cm.rolling_window_days}-day</span>{' '}
            trailing window (sample mean / stdev), evaluated on each trading
            day in the series.
          </p>
          <p className="mt-2 text-fg-muted">
            Underlying field is mid-yield.  Source is Bloomberg; date index
            follows the NY-Fed trading-day calendar.  Both legs use the same
            day-count convention.
          </p>
        </>
      }
    >
      <section className="research-card overflow-hidden">
        <div className="px-5 pt-4 pb-1">
          <div className="kicker text-fg-muted">
            {cm.spread_label}
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
        <PrimitiveMetrics items={items} title="Snapshot" desktopCols={6} />
      </section>
    </PrimitiveCanvasShell>
  );
}
