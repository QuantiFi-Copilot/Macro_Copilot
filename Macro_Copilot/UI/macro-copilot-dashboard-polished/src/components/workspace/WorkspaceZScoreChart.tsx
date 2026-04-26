// ============================================================================
// WorkspaceZScoreChart
// ----------------------------------------------------------------------------
// Secondary chart that visualises the rolling z-score time-series alongside
// the primary value chart.  Stylistic conventions:
//
//   |z| > 2.0  →  coral-400  (extreme)
//   |z| > 1.5  →  amber-400  (stretched)
//   else       →  ice-400    (normal)
//
// Reference bands are drawn at ±1, ±2 to make the magnitude legible at a
// glance.  The line itself is drawn in a neutral tone; coloured dots marking
// extreme observations sit on top so the eye can pick out where the regime
// crossed the thresholds.
// ============================================================================

import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

export type WorkspaceZScorePoint = {
  /** ISO date string (YYYY-MM-DD). */
  date: string;
  /** Rolling z-score on that date. */
  z_score: number | null;
};

type WorkspaceZScoreChartProps = {
  data: WorkspaceZScorePoint[];
  /** Pixel height of the chart.  Default 140 for a "ribbon" feel. */
  height?: number;
};

const LINE_STROKE = '#7AA2FF';        // ice-400
const STRETCHED_FILL = '#F3B755';     // amber-400
const EXTREME_FILL = '#FF6B7E';       // coral-400

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

function ZScoreTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: WorkspaceZScorePoint }>;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const p = payload[0].payload;
  if (p.z_score === null || p.z_score === undefined) return null;
  const abs = Math.abs(p.z_score);
  const tag = abs > 2 ? 'extreme' : abs > 1.5 ? 'stretched' : 'normal';
  const tagColor =
    abs > 2 ? '#FF6B7E' : abs > 1.5 ? '#F3B755' : '#A4A8B6';
  return (
    <div style={TOOLTIP_STYLE}>
      <div className="text-[10px] uppercase tracking-[0.06em] text-fg-faint">
        {formatTooltipDate(p.date)}
      </div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span className="text-[10.5px] text-fg-muted">z-score</span>
        <span className="mono text-[12.5px] font-semibold text-fg-primary">
          {p.z_score.toFixed(2)}
        </span>
      </div>
      <div className="mt-0.5 text-[10px]" style={{ color: tagColor }}>
        {tag}
      </div>
    </div>
  );
}

export function WorkspaceZScoreChart({
  data,
  height = 140,
}: WorkspaceZScoreChartProps) {
  if (data.length === 0) {
    return (
      <div
        style={{ height }}
        className="flex items-center justify-center rounded-md border border-dashed border-line-subtle text-[11px] text-fg-muted"
      >
        No z-score data
      </div>
    );
  }

  // Drop nulls for rendering — the line connects only across known points.
  // Recharts handles nulls reasonably but explicit filtering keeps Scatter
  // payloads clean and avoids surprise gaps in the dot overlays.
  const cleaned = data.filter(
    (d): d is { date: string; z_score: number } => d.z_score !== null && d.z_score !== undefined,
  );

  const stretchedDots = cleaned.filter(
    (d) => Math.abs(d.z_score) > 1.5 && Math.abs(d.z_score) <= 2,
  );
  const extremeDots = cleaned.filter((d) => Math.abs(d.z_score) > 2);

  // Domain — symmetric around zero, anchored to data range with a small
  // headroom but never tighter than ±2.5 so the reference bands are visible.
  const maxAbs = cleaned.reduce(
    (m, d) => Math.max(m, Math.abs(d.z_score)),
    0,
  );
  const domainMax = Math.max(2.5, Math.ceil(maxAbs * 10) / 10 + 0.2);

  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer>
        <ComposedChart data={cleaned} margin={{ top: 4, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid
            stroke="rgba(255,255,255,0.03)"
            strokeDasharray="2 4"
            vertical={false}
          />

          <XAxis
            dataKey="date"
            tick={false}
            axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
            tickLine={false}
          />

          <YAxis
            domain={[-domainMax, domainMax]}
            ticks={[-2, -1, 0, 1, 2]}
            stroke="rgba(255,255,255,0.18)"
            tick={{
              fill: '#6A6E7C',
              fontSize: 10,
              fontFamily: '"JetBrains Mono", ui-monospace, monospace',
            }}
            tickLine={false}
            axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
            width={56}
          />

          {/* Reference bands — zero, ±1 (mild), ±2 (extreme). */}
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.18)" strokeWidth={1} />
          <ReferenceLine
            y={1}
            stroke="rgba(243,183,85,0.25)"
            strokeDasharray="3 3"
          />
          <ReferenceLine
            y={-1}
            stroke="rgba(243,183,85,0.25)"
            strokeDasharray="3 3"
          />
          <ReferenceLine
            y={2}
            stroke="rgba(255,107,126,0.32)"
            strokeDasharray="3 3"
          />
          <ReferenceLine
            y={-2}
            stroke="rgba(255,107,126,0.32)"
            strokeDasharray="3 3"
          />

          <Tooltip
            cursor={{
              stroke: 'rgba(148,163,184,0.25)',
              strokeWidth: 1,
              strokeDasharray: '3 3',
            }}
            content={<ZScoreTooltip />}
          />

          <Line
            type="monotone"
            dataKey="z_score"
            stroke={LINE_STROKE}
            strokeWidth={1.4}
            dot={false}
            isAnimationActive={false}
          />

          {/* Coloured dots overlaid where |z| crosses thresholds. */}
          <Scatter
            data={stretchedDots}
            dataKey="z_score"
            fill={STRETCHED_FILL}
            shape="circle"
            isAnimationActive={false}
          />
          <Scatter
            data={extremeDots}
            dataKey="z_score"
            fill={EXTREME_FILL}
            shape="circle"
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
