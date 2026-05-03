import type { FXScannerResponse } from '@/types/fx';

interface FXScannerViewProps {
  payload: FXScannerResponse;
}

const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

export default function FXScannerView({ payload }: FXScannerViewProps) {
  return (
    <div className="px-6 py-6">
      <div className="card overflow-hidden">
        <div className="border-b border-border-subtle px-5 py-4">
          <div className="text-[13px] font-semibold text-fg-primary">
            FX Scanner
          </div>
          <div className="mt-1 text-[11.5px] text-fg-muted">
            Ranked by absolute spot z-score
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-left text-[12px]">
            <thead className="border-b border-border-subtle bg-bg-secondary text-[11px] uppercase tracking-[0.14em] text-fg-muted">
              <tr>
                <th className="px-5 py-3 font-medium">Pair</th>
                <th className="px-5 py-3 font-medium">Spot</th>
                <th className="px-5 py-3 font-medium">1D</th>
                <th className="px-5 py-3 font-medium">1W</th>
                <th className="px-5 py-3 font-medium">1M</th>
                <th className="px-5 py-3 font-medium">3M</th>
                <th className="px-5 py-3 font-medium">Z-score</th>
                <th className="px-5 py-3 font-medium">Signal</th>
                <th className="px-5 py-3 font-medium">As of</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {payload.rows.map((row) => (
                <tr key={row.pair}>
                  <td className="px-5 py-3 font-semibold text-fg-primary">{row.pair}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.current_spot, row.pair.includes('JPY') ? 2 : 4)}</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.daily_change_pct, 2)}%</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.weekly_change_pct, 2)}%</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.momentum_1m_pct, 2)}%</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.momentum_3m_pct, 2)}%</td>
                  <td className="px-5 py-3 text-fg-secondary">{formatNumber(row.z_score, 2)}</td>
                  <td className="px-5 py-3 text-fg-secondary">{row.signal}</td>
                  <td className="px-5 py-3 text-fg-secondary">{row.as_of_date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {payload.rows.length === 0 ? (
          <div className="px-5 py-8 text-center text-[12px] text-fg-muted">
            No FX scanner rows available.
          </div>
        ) : null}
      </div>
    </div>
  );
}
