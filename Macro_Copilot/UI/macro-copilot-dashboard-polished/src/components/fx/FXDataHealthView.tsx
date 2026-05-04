import type { FXDataHealthResponse } from '@/types/fx';

interface FXDataHealthViewProps {
  payload: FXDataHealthResponse;
}

function statusTone(status: string) {
  const lower = status.toLowerCase();
  if (lower.includes('healthy') || lower.includes('complete') || lower.includes('available')) {
    return 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200';
  }
  if (lower.includes('missing') || lower.includes('stale')) {
    return 'border-coral-300/25 bg-coral-300/10 text-coral-200';
  }
  return 'border-amber-300/25 bg-amber-300/10 text-amber-200';
}

function listText(values: string[]) {
  return values.length ? values.join(', ') : 'none';
}

export default function FXDataHealthView({ payload }: FXDataHealthViewProps) {
  return (
    <div className="px-6 py-6">
      <div className="space-y-4">
        <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
          <div className="card px-5 py-5">
            <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
              {payload.field_name}
            </div>
            <div className="mt-2 text-[24px] font-semibold capitalize text-fg-primary">
              {payload.status}
            </div>
            <div className="mt-1 text-[12px] text-fg-secondary">
              Dataset as of {payload.as_of_date ?? 'n/a'}.
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <Metric label="Instruments" value={String(payload.summary.instrument_count ?? 0)} />
              <Metric label="Pairs" value={String(payload.summary.pair_count ?? 0)} />
              <Metric label="Missing pairs" value={String(payload.summary.missing_pair_count ?? 0)} />
              <Metric label="Stale series" value={String(payload.summary.stale_series_count ?? 0)} />
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {payload.families.map((family) => (
              <div key={family.instrument_type} className="card px-5 py-4">
                <div className="text-[12px] font-semibold text-fg-primary">{family.family}</div>
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <Metric label="Series" value={`${family.series_with_data}/${family.instruments}`} compact />
                  <Metric label="Stale" value={String(family.stale_series)} compact />
                </div>
                <div className="mt-3 text-[11.5px] text-fg-muted">
                  Latest {family.latest_date ?? 'n/a'}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="card overflow-hidden">
            <div className="border-b border-border-subtle px-5 py-4">
              <div className="text-[13px] font-semibold text-fg-primary">
                Pair Coverage
              </div>
              <div className="mt-1 text-[11.5px] text-fg-muted">
                Spot availability, forward tenors, vol tenors and missing fields by pair.
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-left text-[12px]">
                <thead className="border-b border-border-subtle bg-bg-secondary text-[11px] uppercase tracking-[0.14em] text-fg-muted">
                  <tr>
                    <th className="px-5 py-3 font-medium">Pair</th>
                    <th className="px-5 py-3 font-medium">Spot</th>
                    <th className="px-5 py-3 font-medium">Forwards</th>
                    <th className="px-5 py-3 font-medium">Vol</th>
                    <th className="px-5 py-3 font-medium">Obs</th>
                    <th className="px-5 py-3 font-medium">Status</th>
                    <th className="px-5 py-3 font-medium">Missing</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {payload.pairs.map((row) => (
                    <tr key={row.pair}>
                      <td className="px-5 py-3 font-semibold text-fg-primary">{row.pair}</td>
                      <td className="px-5 py-3 text-fg-secondary">
                        {row.has_spot ? row.latest_spot_date ?? 'yes' : 'no'}
                      </td>
                      <td className="px-5 py-3 text-fg-secondary">{listText(row.forward_tenors)}</td>
                      <td className="px-5 py-3 text-fg-secondary">{listText(row.vol_tenors)}</td>
                      <td className="px-5 py-3 mono text-fg-secondary">{row.observation_count}</td>
                      <td className="px-5 py-3">
                        <span
                          className={`inline-flex rounded-md border px-2 py-1 text-[11px] font-semibold capitalize ${statusTone(
                            row.status,
                          )}`}
                        >
                          {row.status}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-fg-secondary">{listText(row.missing)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="space-y-4">
            <SidePanel
              title="Risk Proxies"
              rows={payload.risk_proxies.map(
                (proxy) =>
                  `${proxy.ticker} · ${proxy.status} · ${proxy.observation_count} obs · ${proxy.latest_date ?? 'n/a'}`,
              )}
              empty="No risk proxies found."
            />
            <SidePanel
              title="Missing Pairs"
              rows={payload.missing_pairs}
              empty="No missing spot pairs."
            />
            <SidePanel
              title="Stale Series"
              rows={payload.stale_series}
              empty="No stale series."
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  compact = false,
}: {
  label: string;
  value: string;
  compact?: boolean;
}) {
  return (
    <div className="rounded-[8px] border border-border-subtle bg-bg-secondary px-3 py-3">
      <div className="text-[11px] text-fg-muted">{label}</div>
      <div className={`mt-1 font-semibold text-fg-primary ${compact ? 'text-[12px]' : 'text-[13px]'}`}>
        {value}
      </div>
    </div>
  );
}

function SidePanel({
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

