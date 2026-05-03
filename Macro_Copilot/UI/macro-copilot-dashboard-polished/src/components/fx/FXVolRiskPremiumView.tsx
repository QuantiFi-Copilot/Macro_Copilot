import type { FXVolRiskPremiumResponse } from '@/types/fx';

interface FXVolRiskPremiumViewProps {
  payload: FXVolRiskPremiumResponse;
}

const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

const signalClass: Record<string, string> = {
  'vol rich': 'border-coral-300/25 bg-coral-300/10 text-coral-200',
  'vol cheap': 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200',
  fair: 'border-border-subtle bg-bg-secondary text-fg-secondary',
};

export default function FXVolRiskPremiumView({ payload }: FXVolRiskPremiumViewProps) {
  const metrics = payload.current_metrics;
  const recentRows = payload.time_series
    .filter((row) => row.implied_vol_pct !== null)
    .slice(-12);

  return (
    <div className="px-6 py-6">
      <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-4">
          <div className="card px-5 py-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
                  {metrics.pair} {metrics.tenor}
                </div>
                <div className="mt-2 text-[24px] font-semibold capitalize text-fg-primary">
                  {metrics.signal}
                </div>
                <div className="mt-1 text-[12px] capitalize text-fg-secondary">
                  {metrics.suggested_expression}
                </div>
              </div>
              <div
                className={`rounded-md border px-2.5 py-1 text-[11px] font-semibold capitalize ${
                  signalClass[metrics.signal]
                }`}
              >
                {metrics.signal}
              </div>
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <Metric label="Implied vol" value={`${formatNumber(metrics.implied_vol_pct, 2)}%`} />
              <Metric label="Realized vol" value={`${formatNumber(metrics.realized_vol_annualized_pct, 2)}%`} />
              <Metric label="Premium" value={`${formatNumber(metrics.vol_risk_premium_pct, 2)} vol pts`} />
              <Metric label="Premium z" value={formatNumber(metrics.premium_z_score, 2)} />
            </div>
          </div>

          <div className="card px-5 py-4">
            <div className="text-[12px] font-semibold text-fg-primary">Interpretation</div>
            <div className="mt-3 text-[12px] leading-5 text-fg-secondary">
              Implied vol is compared with trailing realized vol over the selected
              window. A positive premium means options price more volatility than
              recently delivered spot moves.
            </div>
          </div>
        </div>

        <div className="card overflow-hidden">
          <div className="border-b border-border-subtle px-5 py-4">
            <div className="text-[13px] font-semibold text-fg-primary">
              Implied vs Realized
            </div>
            <div className="mt-1 text-[11.5px] text-fg-muted">
              Latest populated observations for implied vol, realized vol and premium.
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-[12px]">
              <thead className="border-b border-border-subtle bg-bg-secondary text-[11px] uppercase tracking-[0.14em] text-fg-muted">
                <tr>
                  <th className="px-5 py-3 font-medium">Date</th>
                  <th className="px-5 py-3 font-medium">Implied</th>
                  <th className="px-5 py-3 font-medium">Realized</th>
                  <th className="px-5 py-3 font-medium">Premium</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {recentRows.map((row) => (
                  <tr key={row.date}>
                    <td className="px-5 py-3 font-semibold text-fg-primary">{row.date}</td>
                    <td className="px-5 py-3 mono text-fg-secondary">{formatNumber(row.implied_vol_pct, 2)}%</td>
                    <td className="px-5 py-3 mono text-fg-secondary">{formatNumber(row.realized_vol_annualized_pct, 2)}%</td>
                    <td className="px-5 py-3 mono font-semibold text-fg-primary">
                      {formatNumber(row.vol_risk_premium_pct, 2)}
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
