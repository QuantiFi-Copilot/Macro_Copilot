import type { FXCarryResponse } from '@/types/fx';

interface FXCarryViewProps {
  payload: FXCarryResponse;
}

const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

export default function FXCarryView({ payload }: FXCarryViewProps) {
  return (
    <div className="px-6 py-6">
      <div className="card overflow-hidden">
        <div className="flex items-center justify-between border-b border-border-subtle px-5 py-4">
          <div>
            <div className="text-[13px] font-semibold text-fg-primary">
              FX Carry
            </div>
            <div className="mt-1 text-[11.5px] text-fg-muted">
              Tenor {payload.tenor}
            </div>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-[12px]">
            <thead className="border-b border-border-subtle bg-bg-secondary text-[11px] uppercase tracking-[0.14em] text-fg-muted">
              <tr>
                <th className="px-5 py-3 font-medium">Pair</th>
                <th className="px-5 py-3 font-medium">Spot</th>
                <th className="px-5 py-3 font-medium">Forward points</th>
                <th className="px-5 py-3 font-medium">Outright</th>
                <th className="px-5 py-3 font-medium">Carry bps</th>
                <th className="px-5 py-3 font-medium">Annualized</th>
                <th className="px-5 py-3 font-medium">Signal</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {payload.rows.map((row) => (
                <tr key={`${row.pair}-${row.tenor}`}>
                  <td className="px-5 py-3 font-semibold text-fg-primary">{row.pair}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.spot, 4)}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.forward_points, 2)}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.outright_forward, 4)}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.carry_bps_spot, 1)}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.carry_annualized_pct, 2)}%</td>
                  <td className="px-5 py-3 text-fg-secondary">{row.carry_signal}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {payload.rows.length === 0 ? (
          <div className="px-5 py-8 text-center text-[12px] text-fg-muted">
            No carry rows available for this tenor.
          </div>
        ) : null}
      </div>
    </div>
  );
}
