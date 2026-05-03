import type { FXForwardCurveResponse } from '@/types/fx';

interface FXForwardCurveViewProps {
  payload: FXForwardCurveResponse;
}

const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

export default function FXForwardCurveView({ payload }: FXForwardCurveViewProps) {
  const spot = payload.rows[0]?.spot;
  const spotDate = payload.rows[0]?.spot_date;

  return (
    <div className="px-6 py-6">
      <div className="card overflow-hidden">
        <div className="flex items-center justify-between border-b border-border-subtle px-5 py-4">
          <div>
            <div className="text-[13px] font-semibold text-fg-primary">
              {payload.pair} Forward Curve
            </div>
            <div className="mt-1 text-[11.5px] text-fg-muted">
              Spot {spot === undefined ? 'n/a' : formatNumber(spot, payload.pair.includes('JPY') ? 2 : 4)}
              {spotDate ? ` · ${spotDate}` : ''}
            </div>
          </div>
        </div>

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
