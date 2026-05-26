// ============================================================================
// PrimitiveChart — primary time-series chart for the Build primitive canvas.
// ----------------------------------------------------------------------------
// Restored from the legacy ``WorkspaceChart`` (phase R2) so the typed
// primitive views (Spread, CrossMarket, Butterfly, Yield, …) on the Build
// surface have a chart with visible axes, hover tooltip with date + value
// + z-score, subtle grid, and a tone keyed to the analysis register.
//
// Reused identically by all primitive views; tone is selected per-view to
// stay consistent with the rest of the Build chassis (ice for inputs,
// violet for transforms, amber for outputs).
// ============================================================================

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { chartStrokeForTone, type ChartTone } from '@/lib/chart';

export type PrimitiveChartPoint = {
  /** ISO date string (YYYY-MM-DD). */
  date: string;
  /** Metric value (bps, percent, anything — caller controls). */
  value: number;
  /** Optional z-score for tooltip context. */
  z_score?: number | null;
};

type Props = {
  data: PrimitiveChartPoint[];
  unit?: string;
  valueDecimals?: number;
  tone?: ChartTone;
  height?: number;
  seriesName?: string;
};

const TOOLTIP_STYLE = {
  backgroundColor: 'rgba(12, 14, 21, 0.96)',
  border: '1px solid rgba(148,163,184,0.18)',
  borderRadius: 8,
  fontSize: 11,
  color: '#E8EAF0',
  fontFamily: '"JetBrains Mono", ui-monospace, monospace',
  boxShadow: '0 12px 36px -12px rgba(0,0,0,0.6)',
  padding: '8px 10px',
} as const;

function formatXAxisTick(value: string, includeYear: boolean): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const month = d.toLocaleString('en-US', { month: 'short' });
  const day = d.getDate();
  if (includeYear) return `${month} '${String(d.getFullYear()).slice(-2)}`;
  return `${month} ${day}`;
}

function formatTooltipDate(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function CustomTooltip({
  active,
  payload,
  unit,
  valueDecimals,
  seriesName,
}: {
  active?: boolean;
  payload?: Array<{ payload: PrimitiveChartPoint }>;
  unit?: string;
  valueDecimals: number;
  seriesName?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0].payload;
  return (
    <div style={TOOLTIP_STYLE}>
      <div className="text-[10px] uppercase tracking-[0.06em] text-fg-faint">
        {formatTooltipDate(p.date)}
      </div>
      <div className="mt-1 flex items-baseline gap-1.5">
        {seriesName ? (
          <span className="text-[10.5px] text-fg-muted">{seriesName}</span>
        ) : null}
        <span className="mono text-[12.5px] font-semibold text-fg-primary">
          {p.value.toFixed(valueDecimals)}
        </span>
        {unit ? (
          <span className="text-[10px] text-fg-muted">{unit}</span>
        ) : null}
      </div>
      {p.z_score !== null && p.z_score !== undefined ? (
        <div className="mt-0.5 text-[10px] text-fg-muted">
          z = {p.z_score.toFixed(2)}
        </div>
      ) : null}
    </div>
  );
}

export function PrimitiveChart({
  data,
  unit = '',
  valueDecimals = 2,
  tone = 'neutral',
  height = 320,
  seriesName,
}: Props) {
  if (data.length === 0) {
    return (
      <div
        style={{ height }}
        className="flex items-center justify-center rounded-md border border-dashed border-line-subtle text-[12px] text-fg-muted"
      >
        No data in window
      </div>
    );
  }

  const stroke = chartStrokeForTone(tone);
  const gradientId = `primchart-${tone}`;

  const firstYear = new Date(data[0].date).getFullYear();
  const lastYear = new Date(data[data.length - 1].date).getFullYear();
  const includeYear = firstYear !== lastYear;

  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 12, right: 16, bottom: 8, left: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.32} />
              <stop offset="60%" stopColor={stroke} stopOpacity={0.08} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>

          <CartesianGrid
            stroke="rgba(255,255,255,0.03)"
            strokeDasharray="2 4"
            vertical={false}
          />

          <XAxis
            dataKey="date"
            tickFormatter={(v) => formatXAxisTick(String(v), includeYear)}
            stroke="rgba(255,255,255,0.18)"
            tick={{ fill: '#6A6E7C', fontSize: 10 }}
            tickLine={false}
            axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
            minTickGap={48}
          />

          <YAxis
            stroke="rgba(255,255,255,0.18)"
            tick={{
              fill: '#6A6E7C',
              fontSize: 10,
              fontFamily: '"JetBrains Mono", ui-monospace, monospace',
            }}
            tickLine={false}
            axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
            tickFormatter={(v) => Number(v).toFixed(valueDecimals)}
            width={56}
          />

          <Tooltip
            cursor={{
              stroke: 'rgba(148,163,184,0.25)',
              strokeWidth: 1,
              strokeDasharray: '3 3',
            }}
            content={
              <CustomTooltip
                unit={unit}
                valueDecimals={valueDecimals}
                seriesName={seriesName}
              />
            }
          />

          <Area
            type="monotone"
            dataKey="value"
            stroke={stroke}
            fill={`url(#${gradientId})`}
            strokeWidth={1.6}
            dot={false}
            activeDot={{ r: 3, fill: stroke, strokeWidth: 0 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
