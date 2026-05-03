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
import type { FXForwardCurveResponse } from '@/types/fx';

interface FXForwardCurveViewProps {
  payload: FXForwardCurveResponse;
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

export default function FXForwardCurveView({ payload }: FXForwardCurveViewProps) {
  const spot = payload.rows[0]?.spot;
  const spotDate = payload.rows[0]?.spot_date;
  const isJpy = payload.pair.includes('JPY');
  const priceDigits = isJpy ? 2 : 4;
  const curveRows = [...payload.rows].sort((a, b) => a.tenor_days - b.tenor_days);

  return (
    <div className="px-6 py-6">
      <div className="card overflow-hidden">
        <div className="flex items-center justify-between border-b border-border-subtle px-5 py-4">
          <div>
            <div className="text-[13px] font-semibold text-fg-primary">
              {payload.pair} Forward Curve
            </div>
            <div className="mt-1 text-[11.5px] text-fg-muted">
              Spot {spot === undefined ? 'n/a' : formatNumber(spot, priceDigits)}
              {spotDate ? ` · ${spotDate}` : ''}
            </div>
          </div>
        </div>

        {curveRows.length > 0 ? (
          <div className="border-b border-border-subtle px-5 py-5">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <div className="text-[12px] font-semibold text-fg-primary">
                  Outright Forward Curve
                </div>
                <div className="mt-1 text-[11px] text-fg-muted">
                  Forward outright by tenor, with spot as reference.
                </div>
              </div>
              <div className="mono text-[11px] text-fg-muted">
                {curveRows[0]?.tenor} - {curveRows[curveRows.length - 1]?.tenor}
              </div>
            </div>

            <div className="h-[320px] w-full">
              <ResponsiveContainer>
                <LineChart
                  data={curveRows}
                  margin={{ top: 12, right: 18, bottom: 8, left: 0 }}
                >
                  <CartesianGrid
                    stroke="rgba(255,255,255,0.04)"
                    strokeDasharray="2 4"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="tenor"
                    stroke="rgba(255,255,255,0.18)"
                    tick={{ fill: '#6A6E7C', fontSize: 10 }}
                    tickLine={false}
                    axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
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
                    tickFormatter={(value) => Number(value).toFixed(priceDigits)}
                    width={62}
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
                      const row = tooltipPayload[0].payload as (typeof curveRows)[number];
                      return (
                        <div style={TOOLTIP_STYLE}>
                          <div className="text-[10px] uppercase tracking-[0.06em] text-fg-faint">
                            {row.tenor} · {row.forward_date}
                          </div>
                          <div className="mt-1 flex items-baseline gap-1.5">
                            <span className="text-[10.5px] text-fg-muted">forward</span>
                            <span className="mono text-[12.5px] font-semibold text-fg-primary">
                              {formatNumber(row.outright_forward, priceDigits)}
                            </span>
                          </div>
                          <div className="mt-0.5 text-[10px] text-fg-muted">
                            points {formatNumber(row.forward_points, 2)} · carry{' '}
                            {formatNumber(row.carry_annualized_pct, 2)}%
                          </div>
                        </div>
                      );
                    }}
                  />
                  {spot !== undefined ? (
                    <ReferenceLine
                      y={spot}
                      stroke="rgba(148,163,184,0.45)"
                      strokeDasharray="4 4"
                      label={{
                        value: 'spot',
                        fill: '#8A90A0',
                        fontSize: 10,
                        position: 'insideTopRight',
                      }}
                    />
                  ) : null}
                  <Line
                    type="monotone"
                    dataKey="outright_forward"
                    stroke="#38BDF8"
                    strokeWidth={2}
                    dot={{ r: 3, fill: '#38BDF8', strokeWidth: 0 }}
                    activeDot={{ r: 4, fill: '#E8EAF0', stroke: '#38BDF8', strokeWidth: 2 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        ) : null}

        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-[12px]">
            <thead className="border-b border-border-subtle bg-bg-secondary text-[11px] uppercase tracking-[0.14em] text-fg-muted">
              <tr>
                <th className="px-5 py-3 font-medium">Tenor</th>
                <th className="px-5 py-3 font-medium">Forward date</th>
                <th className="px-5 py-3 font-medium">Forward points</th>
                <th className="px-5 py-3 font-medium">Spot units</th>
                <th className="px-5 py-3 font-medium">Outright</th>
                <th className="px-5 py-3 font-medium">Carry bps</th>
                <th className="px-5 py-3 font-medium">Annualized</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {payload.rows.map((row) => (
                <tr key={`${row.pair}-${row.tenor}`}>
                  <td className="px-5 py-3 font-semibold text-fg-primary">{row.tenor}</td>
                  <td className="px-5 py-3 text-fg-secondary">{row.forward_date}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.forward_points, 2)}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.forward_points_spot_units, 6)}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.outright_forward, row.pair.includes('JPY') ? 2 : 4)}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.carry_bps_spot, 1)}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.carry_annualized_pct, 2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {payload.rows.length === 0 ? (
          <div className="px-5 py-8 text-center text-[12px] text-fg-muted">
            No forward curve rows available for this pair.
          </div>
        ) : null}
      </div>
    </div>
  );
}
