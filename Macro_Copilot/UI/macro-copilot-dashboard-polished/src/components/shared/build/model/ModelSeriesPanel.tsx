// ============================================================================
// src/components/shared/build/model/ModelSeriesPanel.tsx — model series card.
// ----------------------------------------------------------------------------
// Consolidation target #4: ONE multi-line chart for every model's
// N-series read (PCA factor scores, rolling betas, residual z) so the
// charts read identically across models — same card chrome, same tone
// cycle, same legend-with-current-value header, same axis/tooltip
// treatment (lifted from the PCA pilot's FactorScoresChart).
//
// Finance-blind: the module maps its ``time_series_*`` Outputs onto
// ``ModelSeries[]``; this component never knows what a "factor" is
// (FP13, P3).  FP9: values render unchanged — the only client-side
// computation is the date-union pivot for recharts.
// ============================================================================

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { chartStrokeForTone } from '@/lib/chart';
import { modelToneAt, type ModelSeries } from './types';

export interface ModelSeriesPanelProps {
  /** Card kicker (e.g. "Factor scores · over time", "Rolling betas"). */
  title: string;
  /** One-line explanation under the kicker (P5). */
  description?: string;
  series: ModelSeries[];
  /** Y-axis unit annotation appended to tooltip values (e.g. "ratio"). */
  unit?: string;
  height?: number;
  /** Optional current level per series key, rendered in the legend
   *  header (the "pc1 −1.21" read). */
  currentLevels?: Record<string, number | null>;
  /** Decimals for legend current levels + tooltip. */
  decimals?: number;
  /** Draw a zero reference line (betas / residuals / z-scores read
   *  around zero). */
  zeroLine?: boolean;
}

export function ModelSeriesPanel({
  title,
  description,
  series,
  unit,
  height = 300,
  currentLevels,
  decimals = 2,
  zeroLine = false,
}: ModelSeriesPanelProps) {
  // Pivot the per-series rows into recharts' wide shape on the union
  // of dates (display-only reshaping; values pass through untouched).
  const allDates = new Set<string>();
  for (const s of series) for (const r of s.rows) allDates.add(r.date);
  const sortedDates = [...allDates].sort();

  type WideRow = Record<string, string | number | null>;
  const valueBySeriesAndDate: Record<string, Map<string, number | null>> = {};
  for (const s of series) {
    valueBySeriesAndDate[s.key] = new Map(
      s.rows.map((r) => [r.date, r.value]),
    );
  }
  const wide: WideRow[] = sortedDates.map((d) => {
    const row: WideRow = { date: d };
    for (const s of series) {
      row[s.key] = valueBySeriesAndDate[s.key].get(d) ?? null;
    }
    return row;
  });

  if (wide.length === 0 || series.length === 0) return null;

  return (
    <section className="card overflow-hidden">
      <div className="flex items-baseline justify-between gap-3 border-b border-line-subtle px-5 py-3">
        <div className="min-w-0">
          <div className="kicker">{title}</div>
          {description && (
            <p className="mt-0.5 text-[10.5px] text-fg-muted">{description}</p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap items-baseline justify-end gap-3 text-[10.5px]">
          {series.map((s, i) => {
            const stroke = chartStrokeForTone(s.tone ?? modelToneAt(i));
            const v = currentLevels?.[s.key];
            return (
              <span key={s.key} className="flex items-center gap-1.5">
                <span
                  aria-hidden
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: stroke }}
                />
                <span className="mono text-fg-secondary">
                  {s.label ?? s.key}
                </span>
                {currentLevels && (
                  <span className="mono text-fg-faint">
                    {typeof v === 'number' && Number.isFinite(v)
                      ? v.toFixed(decimals)
                      : '—'}
                  </span>
                )}
              </span>
            );
          })}
        </div>
      </div>
      <div className="px-3 py-3">
        <ResponsiveContainer width="100%" height={height}>
          <LineChart
            data={wide}
            margin={{ top: 5, right: 18, left: 0, bottom: 5 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
            <XAxis
              dataKey="date"
              tickFormatter={(v) => formatChartDate(v as string)}
              tick={{ fill: 'rgba(148,163,184,0.6)', fontSize: 10 }}
              minTickGap={50}
            />
            <YAxis
              tick={{ fill: 'rgba(148,163,184,0.6)', fontSize: 10 }}
              width={42}
            />
            {zeroLine && (
              <ReferenceLine y={0} stroke="rgba(148,163,184,0.25)" />
            )}
            <Tooltip
              formatter={(value: number | string) =>
                typeof value === 'number'
                  ? `${value.toFixed(decimals)}${unit ? ` ${unit}` : ''}`
                  : value
              }
              contentStyle={{
                backgroundColor: 'rgba(12,14,21,0.96)',
                border: '1px solid rgba(148,163,184,0.18)',
                borderRadius: 8,
                fontSize: 11,
                color: '#E8EAF0',
              }}
            />
            {series.map((s, i) => (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.label ?? s.key}
                stroke={chartStrokeForTone(s.tone ?? modelToneAt(i))}
                strokeWidth={1.4}
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function formatChartDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: '2-digit',
  });
}
