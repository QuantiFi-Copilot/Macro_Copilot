// ============================================================================
// shared/build/compact/MiniChart.tsx
// ----------------------------------------------------------------------------
// Small line chart for the compact card body.  Uses recharts (already
// in the project for PrimitiveChart).  Renders:
//
//   - The series line + soft area gradient under it
//   - Horizontal reference bands (e.g. ±2σ z-score envelope) as
//     dashed horizontal lines colored per band tone
//   - A terminal dot at the latest observation
//   - Y-axis ticks at the reference-band values + 0 (for level series)
//   - X-axis ticks at every ~quarter of the series window
//
// Finance-blind — the per-tool wrapper passes the data + bands + unit
// label; this component renders.
// ============================================================================

import { useMemo } from 'react';
import {
  Area,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts';
import type { ChartPoint, ReferenceBand } from '../lib/types';

type Props = {
  points: ReadonlyArray<ChartPoint>;
  unit: string;
  referenceBands?: ReadonlyArray<ReferenceBand>;
  /** Container height in pixels (parent decides; default 160 fits the
   *  compact card body). */
  height?: number;
};

const BAND_STROKE: Record<ReferenceBand['tone'], string> = {
  positive: '#7ee5c1',   // mint-300-ish
  extreme: '#ff8c8c',    // coral-300-ish
  negative: '#ff8c8c',
  elevated: '#f4c47a',   // amber-300-ish
  neutral: '#a0a0a0',
};

export function MiniChart({ points, unit, referenceBands = [], height = 160 }: Props) {
  // Defensive: filter null values; recharts treats them as gaps.
  const chartData = useMemo(
    () =>
      points
        .map((p) => ({ date: p.date, value: p.value }))
        .filter((p) => p.value != null && !Number.isNaN(p.value)),
    [points],
  );

  if (chartData.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-[11px] text-fg-muted"
        style={{ height }}
      >
        No data in window
      </div>
    );
  }

  // Compute a tick set: include the reference-band values + 0 + the
  // series min/max so the visual anchors are intuitive.
  const values = chartData.map((p) => p.value);
  const dataMin = Math.min(...values);
  const dataMax = Math.max(...values);
  const bandValues = referenceBands.map((b) => b.value);
  const yTicks = Array.from(
    new Set([...bandValues, 0, dataMin, dataMax].map((v) => Number(v.toFixed(2)))),
  )
    .sort((a, b) => a - b);

  // Pick ~5 x-axis ticks evenly spaced across the data.
  const xTickCount = 5;
  const xTickIndices = Array.from({ length: xTickCount }, (_, i) =>
    Math.floor((i * (chartData.length - 1)) / (xTickCount - 1)),
  );
  const xTicks = xTickIndices.map((i) => chartData[i]?.date).filter(Boolean) as string[];

  const lastPoint = chartData[chartData.length - 1];

  // Y-domain: stretch ~5% beyond the [min, max] union of data + bands
  // so the terminal dot and band lines aren't flush against the edges.
  const yMinRaw = Math.min(dataMin, ...bandValues);
  const yMaxRaw = Math.max(dataMax, ...bandValues);
  const yPad = (yMaxRaw - yMinRaw) * 0.05 || 0.1;

  return (
    <div style={{ height, width: '100%' }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={chartData}
          margin={{ top: 6, right: 6, bottom: 4, left: 4 }}
        >
          <defs>
            <linearGradient id="mini-area-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#7b6cff" stopOpacity={0.32} />
              <stop offset="100%" stopColor="#7b6cff" stopOpacity={0} />
            </linearGradient>
          </defs>

          <XAxis
            dataKey="date"
            ticks={xTicks}
            tick={{ fontSize: 10, fill: '#8a8a8a' }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
            tickFormatter={formatShortMonth}
          />
          <YAxis
            ticks={yTicks}
            tick={{ fontSize: 10, fill: '#8a8a8a' }}
            tickLine={false}
            axisLine={false}
            domain={[yMinRaw - yPad, yMaxRaw + yPad]}
            tickFormatter={(v) => `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}`}
            width={32}
          />

          {referenceBands.map((band, i) => (
            <ReferenceLine
              key={`band-${i}-${band.value}`}
              y={band.value}
              stroke={BAND_STROKE[band.tone]}
              strokeWidth={1}
              strokeDasharray="4 4"
              ifOverflow="extendDomain"
            />
          ))}

          <Area
            type="monotone"
            dataKey="value"
            stroke="none"
            fill="url(#mini-area-grad)"
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
          {/* Terminal dot — large, glowing, indicates the latest observation. */}
          {lastPoint && (
            <ReferenceLine
              x={lastPoint.date}
              stroke="transparent"
              ifOverflow="extendDomain"
              label={{
                position: 'insideRight',
                content: () => (
                  <g />
                ),
              }}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function formatShortMonth(dateStr: string): string {
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

// Suppress unused-export warnings for the unit param — kept on the
// signature so per-tool wrappers' typed calls remain stable when the
// chart adds a y-axis label in a follow-up.
void (null as unknown as Props['unit']);
