import type { FXRealizedVolResponse } from '@/types/fx';

interface FXRealizedVolViewProps {
  payload: FXRealizedVolResponse;
}

const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

export default function FXRealizedVolView({ payload }: FXRealizedVolViewProps) {
  const metrics = payload.current_metrics;
  const recentRows = payload.time_series
    .filter((row) => row.realized_vol_annualized_pct !== null)
    .slice(-10);

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
            <div className="text-[13px] font-semibold text-fg-primary">
              Recent Realized Vol
            </div>
            <div className="mt-1 text-[11.5px] text-fg-muted">
              Last 10 populated observations
            </div>
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
