import type { FXCorrelationBetaResponse } from '@/types/fx';

interface FXCorrelationBetaViewProps {
  payload: FXCorrelationBetaResponse;
}

const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

function corrTone(value: number | null) {
  if (value === null) return 'text-fg-muted';
  if (value >= 0.35) return 'text-emerald-200';
  if (value <= -0.35) return 'text-coral-200';
  return 'text-fg-secondary';
}

function sensitivityTone(label: string) {
  const lower = label.toLowerCase();
  if (lower.includes('high')) return 'border-coral-300/25 bg-coral-300/10 text-coral-200';
  if (lower.includes('medium')) return 'border-amber-300/25 bg-amber-300/10 text-amber-200';
  return 'border-border-subtle bg-bg-secondary text-fg-secondary';
}

export default function FXCorrelationBetaView({ payload }: FXCorrelationBetaViewProps) {
  return (
    <div className="px-6 py-6">
      <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-4">
          <div className="card px-5 py-5">
            <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
              {payload.pair}
            </div>
            <div className="mt-2 text-[24px] font-semibold text-fg-primary">
              {payload.dominant_driver ?? 'No dominant driver'}
            </div>
            <div className="mt-1 text-[12px] leading-5 text-fg-secondary">
              {payload.summary}
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <Metric label="Window" value={`${payload.window_observations} obs`} />
              <Metric label="As of" value={payload.as_of_date} />
              <Metric
                label="Dominant corr"
                value={formatNumber(payload.dominant_correlation, 2)}
                valueClass={corrTone(payload.dominant_correlation)}
              />
              <Metric label="Drivers" value={String(payload.rows.length)} />
            </div>
          </div>

          <DetailPanel title="Risks" rows={payload.risks} empty="No high-beta risks flagged." />
          <DetailPanel title="Follow-Ups" rows={payload.follow_ups} empty="No follow-ups suggested." />
        </div>

        <div className="card overflow-hidden">
          <div className="border-b border-border-subtle px-5 py-4">
            <div className="text-[13px] font-semibold text-fg-primary">
              Macro Betas
            </div>
            <div className="mt-1 text-[11.5px] text-fg-muted">
              Return correlation and simple OLS beta of {payload.pair} versus macro proxies.
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-[12px]">
              <thead className="border-b border-border-subtle bg-bg-secondary text-[11px] uppercase tracking-[0.14em] text-fg-muted">
                <tr>
                  <th className="px-5 py-3 font-medium">Proxy</th>
                  <th className="px-5 py-3 font-medium">Corr</th>
                  <th className="px-5 py-3 font-medium">Beta</th>
                  <th className="px-5 py-3 font-medium">R²</th>
                  <th className="px-5 py-3 font-medium">1M move</th>
                  <th className="px-5 py-3 font-medium">Sensitivity</th>
                  <th className="px-5 py-3 font-medium">Obs</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {payload.rows.map((row) => (
                  <tr key={row.ticker}>
                    <td className="px-5 py-3">
                      <div className="font-semibold text-fg-primary">{row.label}</div>
                      <div className="mt-0.5 text-[11px] text-fg-muted">
                        {row.ticker} · {row.proxy_family}
                      </div>
                      <div className="mt-1 max-w-[320px] text-[11.5px] leading-4 text-fg-secondary">
                        {row.interpretation}
                      </div>
                    </td>
                    <td className={`px-5 py-3 mono font-semibold ${corrTone(row.correlation)}`}>
                      {formatNumber(row.correlation, 2)}
                    </td>
                    <td className="px-5 py-3 mono text-fg-secondary">
                      {formatNumber(row.beta, 2)}
                    </td>
                    <td className="px-5 py-3 mono text-fg-secondary">
                      {formatNumber(row.r_squared, 2)}
                    </td>
                    <td className="px-5 py-3 mono text-fg-secondary">
                      {formatNumber(row.proxy_1m_change_pct, 2)}%
                    </td>
                    <td className="px-5 py-3">
                      <span
                        className={`inline-flex rounded-md border px-2 py-1 text-[11px] font-semibold capitalize ${sensitivityTone(
                          row.sensitivity_label,
                        )}`}
                      >
                        {row.sensitivity_label}
                      </span>
                    </td>
                    <td className="px-5 py-3 mono text-fg-secondary">{row.observations}</td>
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

function Metric({
  label,
  value,
  valueClass = 'text-fg-primary',
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded-[8px] border border-border-subtle bg-bg-secondary px-3 py-3">
      <div className="text-[11px] text-fg-muted">{label}</div>
      <div className={`mt-1 text-[13px] font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}

function DetailPanel({
  title,
  rows,
  empty,
}: {
  title: string;
  rows: string[];
  empty: string;
}) {
  return (
    <div className="card px-5 py-4">
      <div className="text-[12px] font-semibold text-fg-primary">{title}</div>
      <div className="mt-3 space-y-2">
        {rows.length === 0 ? (
          <div className="text-[12px] leading-5 text-fg-muted">{empty}</div>
        ) : (
          rows.map((row) => (
            <div key={row} className="text-[12px] leading-5 text-fg-secondary">
              {row}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

