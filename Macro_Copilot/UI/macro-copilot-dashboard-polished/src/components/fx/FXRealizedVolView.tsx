import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { FXRealizedVolResponse } from '@/types/fx';

interface FXRealizedVolViewProps {
  payload: FXRealizedVolResponse;
}

const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

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

function formatDateTick(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export default function FXRealizedVolView({ payload }: FXRealizedVolViewProps) {
  const metrics = payload.current_metrics;
  const chartRows = payload.time_series
    .filter((row) => row.realized_vol_annualized_pct !== null)
    .map((row) => ({
      date: row.date,
      realized_vol_annualized_pct: row.realized_vol_annualized_pct as number,
    }));
  const recentRows = payload.time_series
    .filter((row) => row.realized_vol_annualized_pct !== null)
    .slice(-10);
  const latestVol = metrics.realized_vol_annualized_pct ?? undefined;

  return (
    <div className="px-6 py-6">
      <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
        <div className="card px-5 py-4">
          <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
            {metrics.pair}
          </div>
          <div className="mt-2 text-[28px] font-semibold text-fg-primary">
            {formatNumber(metrics.realized_vol_annualized_pct, 2)}%
          </div>
          <div className="mt-1 text-[12px] text-fg-secondary">
            {metrics.window_observations}d realized vol · {metrics.as_of_date}
          </div>

          <div className="mt-5 grid gap-3">
            <Metric label="Spot" value={formatNumber(metrics.spot, metrics.pair.includes('JPY') ? 2 : 4)} />
            <Metric label="Daily return" value={`${formatNumber(metrics.daily_return_pct, 2)}%`} />
            <Metric label="Vol z-score" value={formatNumber(metrics.realized_vol_z_score, 2)} />
            <Metric label="Observations" value={String(metrics.observation_count)} />
          </div>
        </div>

        <div className="card overflow-hidden">
          <div className="border-b border-border-subtle px-5 py-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-[13px] font-semibold text-fg-primary">
                  Realized Volatility Trend
                </div>
                <div className="mt-1 text-[11.5px] text-fg-muted">
                  Annualized realized vol over the available window.
                </div>
              </div>
              <div className="mono text-[11px] text-fg-muted">
                z {formatNumber(metrics.realized_vol_z_score, 2)}
              </div>
            </div>
          </div>

          <div className="border-b border-border-subtle px-5 py-5">
            {chartRows.length > 0 ? (
              <div className="h-[320px] w-full">
                <ResponsiveContainer>
                  <AreaChart
                    data={chartRows}
                    margin={{ top: 12, right: 18, bottom: 8, left: 0 }}
                  >
                    <defs>
                      <linearGradient id="fx-realized-vol-fill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#F59E0B" stopOpacity={0.32} />
                        <stop offset="60%" stopColor="#F59E0B" stopOpacity={0.08} />
                        <stop offset="100%" stopColor="#F59E0B" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid
                      stroke="rgba(255,255,255,0.04)"
                      strokeDasharray="2 4"
                      vertical={false}
                    />
                    <XAxis
                      dataKey="date"
                      tickFormatter={formatDateTick}
                      stroke="rgba(255,255,255,0.18)"
                      tick={{ fill: '#6A6E7C', fontSize: 10 }}
                      tickLine={false}
                      axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                      minTickGap={42}
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
                      tickFormatter={(value) => `${Number(value).toFixed(1)}%`}
                      width={58}
                      domain={['auto', 'auto']}
                    />
                    <Tooltip
                      cursor={{
                        stroke: 'rgba(148,163,184,0.25)',
                        strokeWidth: 1,
                        strokeDasharray: '3 3',
                      }}
                      content={({ active, payload: tooltipPayload }) => {
                        if (!active || !tooltipPayload?.length) return null;
                        const row = tooltipPayload[0].payload as (typeof chartRows)[number];
                        return (
                          <div style={TOOLTIP_STYLE}>
                            <div className="text-[10px] uppercase tracking-[0.06em] text-fg-faint">
                              {row.date}
                            </div>
                            <div className="mt-1 flex items-baseline gap-1.5">
                              <span className="text-[10.5px] text-fg-muted">realized vol</span>
                              <span className="mono text-[12.5px] font-semibold text-fg-primary">
                                {formatNumber(row.realized_vol_annualized_pct, 2)}
                              </span>
                              <span className="text-[10px] text-fg-muted">%</span>
                            </div>
                          </div>
                        );
                      }}
                    />
                    {latestVol !== undefined ? (
                      <ReferenceLine
                        y={latestVol}
                        stroke="rgba(245,158,11,0.42)"
                        strokeDasharray="4 4"
                        label={{
                          value: 'latest',
                          fill: '#B8A16A',
                          fontSize: 10,
                          position: 'insideTopRight',
                        }}
                      />
                    ) : null}
                    <Area
                      type="monotone"
                      dataKey="realized_vol_annualized_pct"
                      stroke="#F59E0B"
                      fill="url(#fx-realized-vol-fill)"
                      strokeWidth={1.8}
                      dot={false}
                      activeDot={{ r: 4, fill: '#E8EAF0', stroke: '#F59E0B', strokeWidth: 2 }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="flex h-[320px] items-center justify-center rounded-md border border-dashed border-line-subtle text-[12px] text-fg-muted">
                No realized-volatility history available.
              </div>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[420px] text-left text-[12px]">
              <thead className="border-b border-border-subtle bg-bg-secondary text-[11px] uppercase tracking-[0.14em] text-fg-muted">
                <tr>
                  <th className="px-5 py-3 font-medium">Date</th>
                  <th className="px-5 py-3 font-medium">Annualized vol</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {recentRows.map((row) => (
                  <tr key={row.date}>
                    <td className="px-5 py-3 font-semibold text-fg-primary">{row.date}</td>
                    <td className="px-5 py-3 text-fg-secondary">
                      {formatNumber(row.realized_vol_annualized_pct, 2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[8px] border border-border-subtle bg-bg-secondary px-3 py-3">
      <div className="text-[11px] text-fg-muted">{label}</div>
      <div className="mt-1 text-[13px] font-semibold text-fg-primary">{value}</div>
    </div>
  );
}
