// ============================================================================
// shared/build/extended/MainChart.tsx
// ----------------------------------------------------------------------------
// The full-size time-series chart for the extended Build canvas.  Same
// shape as MiniChart but larger envelope, with:
//
//   - Time-range chips (1M / 6M / YTD / 1Y / 3Y / 5Y / MAX) that filter
//     the displayed slice
//   - Hover tooltip with the date + value + (optional) z-band annotation
//   - Larger y-axis ticks + grid
//
// Finance-blind — accepts ChartPoint[] + ReferenceBand[].  The per-tool
// wrapper decides which series + bands to pass.
// ============================================================================

import { useMemo, useState } from 'react';
import {
  Area,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { ChartPoint, ReferenceBand } from '../lib/types';

type Props = {
  points: ReadonlyArray<ChartPoint>;
  unit: string;
  valueDecimals?: number;
  referenceBands?: ReadonlyArray<ReferenceBand>;
  /** Per-tool override of the chip set.  Default is the standard
   *  range chip set below. */
  rangeChips?: ReadonlyArray<RangeChip>;
  /** Initial selected chip — defaults to "1Y". */
  initialRange?: string;
  /** Header label (e.g. "REAL YIELD HISTORY"). */
  header?: string;
};

type RangeChip = {
  id: string;
  /** Number of calendar days to include from the latest observation
   *  backward.  Special value ``null`` = MAX (no filtering). */
  days: number | null;
};

const DEFAULT_CHIPS: ReadonlyArray<RangeChip> = [
  { id: '1M', days: 30 },
  { id: '6M', days: 182 },
  { id: 'YTD', days: -1 }, // sentinel for year-to-date
  { id: '1Y', days: 365 },
  { id: '3Y', days: 1095 },
  { id: '5Y', days: 1825 },
  { id: 'MAX', days: null },
];

const BAND_STROKE: Record<ReferenceBand['tone'], string> = {
  positive: '#7ee5c1',
  extreme: '#ff8c8c',
  negative: '#ff8c8c',
  elevated: '#f4c47a',
  neutral: '#a0a0a0',
};

export function MainChart({
  points,
  unit,
  valueDecimals = 2,
  referenceBands = [],
  rangeChips = DEFAULT_CHIPS,
  initialRange = '1Y',
  header = 'TIME SERIES',
}: Props) {
  const [selectedRange, setSelectedRange] = useState(initialRange);

  // Filter chart data by the selected range.
  const chartData = useMemo(
    () => filterByRange(points, rangeChips.find((c) => c.id === selectedRange) ?? null),
    [points, rangeChips, selectedRange],
  );

  if (chartData.length === 0) {
    return (
      <section className="card flex h-[360px] items-center justify-center">
        <p className="text-[12px] text-fg-muted">No data in window</p>
      </section>
    );
  }

  const values = chartData.map((p) => p.value);
  const dataMin = Math.min(...values);
  const dataMax = Math.max(...values);
  const bandValues = referenceBands.map((b) => b.value);
  const yPad = (dataMax - dataMin) * 0.05 || 0.1;

  return (
    <section className="card flex flex-col gap-3 px-5 py-4">
      {/* Header row */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="kicker text-fg-muted">{header}</h3>
        <div className="flex items-center gap-1">
          {rangeChips.map((chip) => (
            <button
              key={chip.id}
              type="button"
              onClick={() => setSelectedRange(chip.id)}
              className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors ${
                selectedRange === chip.id
                  ? 'bg-ice-300/15 text-ice-300'
                  : 'text-fg-muted hover:bg-line-subtle/40 hover:text-fg-secondary'
              }`}
            >
              {chip.id}
            </button>
          ))}
        </div>
      </div>

      {/* Series legend */}
      <div className="flex items-center gap-2 text-[11px] text-fg-secondary">
        <span
          className="inline-block h-2 w-2 rounded-full"
          style={{ backgroundColor: '#7b6cff' }}
          aria-hidden
        />
        <span>{header.toLowerCase()} ({unit})</span>
      </div>

      <div className="h-[320px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={chartData}
            margin={{ top: 10, right: 12, bottom: 8, left: 0 }}
          >
            <defs>
              <linearGradient id="main-area-grad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#7b6cff" stopOpacity={0.28} />
                <stop offset="100%" stopColor="#7b6cff" stopOpacity={0} />
              </linearGradient>
            </defs>

            <XAxis
              dataKey="date"
              tick={{ fontSize: 10.5, fill: '#8a8a8a' }}
              tickLine={false}
              axisLine={false}
              minTickGap={48}
              tickFormatter={formatMonthYear}
            />
            <YAxis
              tick={{ fontSize: 10.5, fill: '#8a8a8a' }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v) =>
                `${(v as number) > 0 ? '' : ''}${(v as number).toFixed(1)}${unit === '%' ? '%' : ''}`
              }
              domain={[dataMin - yPad, dataMax + yPad]}
              width={48}
            />

            {referenceBands.map((band, i) => (
              <ReferenceLine
                key={`band-${i}-${band.value}`}
                y={band.value}
                stroke={BAND_STROKE[band.tone]}
                strokeWidth={1}
                strokeDasharray="4 4"
                label={
                  band.label
                    ? {
                        value: band.label,
                        position: 'insideTopRight',
                        style: {
                          fontSize: 10,
                          fill: BAND_STROKE[band.tone],
                          opacity: 0.85,
                        },
                      }
                    : undefined
                }
              />
            ))}

            <Tooltip
              cursor={{ stroke: '#7b6cff', strokeWidth: 1, strokeOpacity: 0.4 }}
              contentStyle={{
                background: 'var(--surface-overlay, #1a1a1a)',
                border: '1px solid var(--line-subtle, #2a2a2a)',
                borderRadius: '6px',
                fontSize: '11px',
                color: 'var(--fg-primary, #e0e0e0)',
              }}
              formatter={(value: number) => [
                `${value > 0 ? '+' : ''}${value.toFixed(valueDecimals)}${unit}`,
                header.toLowerCase(),
              ]}
              labelFormatter={(label: string) => label}
            />

            <Area
              type="monotone"
              dataKey="value"
              stroke="none"
              fill="url(#main-area-grad)"
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke="#7b6cff"
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function filterByRange(
  points: ReadonlyArray<ChartPoint>,
  chip: RangeChip | null,
): ChartPoint[] {
  const cleaned = points.filter((p) => p.value != null && !Number.isNaN(p.value));
  if (!chip || chip.days == null) return [...cleaned];
  if (cleaned.length === 0) return [];

  const last = cleaned[cleaned.length - 1];
  const lastDate = new Date(last.date + 'T00:00:00Z');
  let cutoff: Date;

  if (chip.id === 'YTD') {
    cutoff = new Date(Date.UTC(lastDate.getUTCFullYear(), 0, 1));
  } else {
    cutoff = new Date(lastDate.getTime() - chip.days * 24 * 60 * 60 * 1000);
  }

  const cutoffStr = cutoff.toISOString().slice(0, 10);
  return cleaned.filter((p) => p.date >= cutoffStr);
}

function formatMonthYear(dateStr: string): string {
  try {
    const d = new Date(dateStr + 'T00:00:00Z');
    if (Number.isNaN(d.getTime())) return dateStr;
    const month = d.toLocaleString('en-US', { month: 'short' });
    const year = String(d.getFullYear()).slice(2);
    return `${month} '${year}`;
  } catch {
    return dateStr;
  }
}
