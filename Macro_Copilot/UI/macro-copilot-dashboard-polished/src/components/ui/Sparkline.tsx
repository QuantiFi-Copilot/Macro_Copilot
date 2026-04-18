import {
  Area,
  AreaChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
} from 'recharts';
import type { SparklinePoint } from '@/types/dashboard';
import { chartStrokeForTone, type ChartTone } from '@/lib/chart';

type SparklineProps = {
  data: SparklinePoint[];
  tone?: ChartTone;
  mode?: 'line' | 'area';
  height?: number;
  strokeWidth?: number;
};

export function Sparkline({
  data,
  tone = 'neutral',
  mode = 'area',
  height = 72,
  strokeWidth = 1.4,
}: SparklineProps) {
  const stroke = chartStrokeForTone(tone);
  const gradientId = `fill-${tone}-${mode}`;

  const tooltipStyle = {
    backgroundColor: 'rgba(12, 14, 21, 0.96)',
    border: '1px solid rgba(148,163,184,0.14)',
    borderRadius: 8,
    fontSize: 11,
    color: '#E8EAF0',
    fontFamily: '"JetBrains Mono", ui-monospace, monospace',
    boxShadow: '0 12px 36px -12px rgba(0,0,0,0.6)',
  } as const;

  return (
    <div style={{ width: '100%', height }}>
      <ResponsiveContainer>
        {mode === 'area' ? (
          <AreaChart data={data} margin={{ top: 4, right: 2, bottom: 2, left: 2 }}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={stroke} stopOpacity={0.28} />
                <stop offset="60%" stopColor={stroke} stopOpacity={0.08} />
                <stop offset="100%" stopColor={stroke} stopOpacity={0} />
              </linearGradient>
            </defs>
            <Tooltip
              cursor={{ stroke: 'rgba(148,163,184,0.2)', strokeWidth: 1 }}
              contentStyle={tooltipStyle}
              labelStyle={{ color: '#6A6E7C' }}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke={stroke}
              fill={`url(#${gradientId})`}
              strokeWidth={strokeWidth}
              dot={false}
              activeDot={{ r: 2.5, fill: stroke, strokeWidth: 0 }}
            />
          </AreaChart>
        ) : (
          <LineChart data={data} margin={{ top: 4, right: 2, bottom: 2, left: 2 }}>
            <Tooltip
              cursor={{ stroke: 'rgba(148,163,184,0.2)', strokeWidth: 1 }}
              contentStyle={tooltipStyle}
              labelStyle={{ color: '#6A6E7C' }}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke={stroke}
              strokeWidth={strokeWidth}
              dot={false}
              activeDot={{ r: 2.5, fill: stroke, strokeWidth: 0 }}
            />
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
