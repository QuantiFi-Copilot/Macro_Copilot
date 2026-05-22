// ============================================================================
// ButterflyPrimitiveView — view for ``calculate_butterfly_tool``.
// ----------------------------------------------------------------------------
// 3-leg curvature trade.  Reshell of the legacy ButterflyView onto
// PrimitiveCanvasShell (phase R2).
// ============================================================================

import type { ButterflyOutput } from '@/types/rates';
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
  payload: ButterflyOutput;
};

export function ButterflyPrimitiveView({ payload }: Props) {
  const { current_metrics: cm, time_series } = payload;

  const chartPoints: PrimitiveChartPoint[] = time_series.map((row) => ({
    date: row.date,
    value: row.butterfly_bps,
    z_score: row.z_score ?? null,
  }));

  const zPoints: PrimitiveZScorePoint[] = time_series.map((row) => ({
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
    <PrimitiveCanvasShell
      kicker="Primitive · Butterfly"
      title={`${cm.curve_family} ${cm.butterfly_label}`}
      subtitle="Belly-vs-wings curvature across three tenors."
      asOfDate={cm.as_of_date ?? null}
      meta={<ToolNameChip tool="calculate_butterfly_tool" />}
      methodology={
        <p>
          <span className="font-medium text-fg-primary">
            Butterfly = 2 × Belly yield − (Short yield + Long yield)
          </span>, in basis points.  Positive values mean the belly is rich
          relative to the wings; negative means cheap.  Z-score uses a{' '}
          <span className="font-mono">{cm.rolling_window_days}-day</span>{' '}
          rolling window.
        </p>
      }
    >
      <section className="research-card overflow-hidden">
        <div className="px-5 pt-4 pb-1">
          <div className="kicker text-fg-muted">
            {cm.curve_family} · {cm.butterfly_label}
          </div>
        </div>
        <div className="px-2 pt-2">
          <PrimitiveChart
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
