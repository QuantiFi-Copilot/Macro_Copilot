import type { FXMacroRiskOverlayResponse } from '@/types/fx';

interface FXMacroRiskOverlayViewProps {
  payload: FXMacroRiskOverlayResponse;
}

const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

function corrTone(value: number | null) {
  if (value === null) return 'text-fg-muted';
  if (value > 0.35) return 'text-emerald-200';
  if (value < -0.35) return 'text-coral-200';
  return 'text-fg-secondary';
}

export default function FXMacroRiskOverlayView({ payload }: FXMacroRiskOverlayViewProps) {
  const spotDigits = payload.pair.includes('JPY') ? 2 : 4;

  return (
    <div className="px-6 py-6">
      <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-4">
          <div className="card px-5 py-5">
            <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
              {payload.pair}
            </div>
            <div className="mt-2 text-[24px] font-semibold capitalize text-fg-primary">
              {payload.risk_regime}
            </div>
            <div className="mt-1 text-[12px] leading-5 text-fg-secondary">
              {payload.summary}
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <Metric label="Spot" value={formatNumber(payload.spot, spotDigits)} />
              <Metric
                label="Risk score"
                value={`${payload.regime_score > 0 ? '+' : ''}${formatNumber(payload.regime_score, 2)}`}
              />
            </div>
          </div>

          <div className="card px-5 py-4">
            <div className="text-[12px] font-semibold text-fg-primary">Implications</div>
            <div className="mt-3 space-y-2">
              {payload.implications.map((item) => (
                <div key={item} className="text-[12px] leading-5 text-fg-secondary">
                  {item}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="card overflow-hidden">
          <div className="border-b border-border-subtle px-5 py-4">
            <div className="text-[13px] font-semibold text-fg-primary">
              Macro Proxy Stack
            </div>
            <div className="mt-1 text-[11.5px] text-fg-muted">
              Latest levels, momentum, z-scores and return correlation to {payload.pair}.
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-[12px]">
              <thead className="border-b border-border-subtle bg-bg-secondary text-[11px] uppercase tracking-[0.14em] text-fg-muted">
                <tr>
                  <th className="px-5 py-3 font-medium">Proxy</th>
                  <th className="px-5 py-3 font-medium">Level</th>
                  <th className="px-5 py-3 font-medium">1d</th>
                  <th className="px-5 py-3 font-medium">1m</th>
                  <th className="px-5 py-3 font-medium">3m</th>
                  <th className="px-5 py-3 font-medium">z-score</th>
                  <th className="px-5 py-3 font-medium">corr</th>
                  <th className="px-5 py-3 font-medium">As of</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {payload.proxy_rows.map((row) => (
                  <tr key={row.ticker}>
                    <td className="px-5 py-3">
                      <div className="font-semibold text-fg-primary">{row.label}</div>
                      <div className="mt-0.5 text-[11px] text-fg-muted">{row.ticker}</div>
                    </td>
                    <td className="px-5 py-3 mono text-fg-secondary">{formatNumber(row.level, 2)}</td>
                    <td className="px-5 py-3 mono text-fg-secondary">{formatNumber(row.daily_change_pct, 2)}%</td>
                    <td className="px-5 py-3 mono text-fg-secondary">{formatNumber(row.monthly_change_pct, 2)}%</td>
                    <td className="px-5 py-3 mono text-fg-secondary">{formatNumber(row.three_month_change_pct, 2)}%</td>
                    <td className="px-5 py-3 mono text-fg-secondary">{formatNumber(row.z_score, 2)}</td>
                    <td className={`px-5 py-3 mono font-semibold ${corrTone(row.correlation_to_pair)}`}>
                      {formatNumber(row.correlation_to_pair, 2)}
                    </td>
                    <td className="px-5 py-3 text-fg-secondary">{row.as_of_date}</td>
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
