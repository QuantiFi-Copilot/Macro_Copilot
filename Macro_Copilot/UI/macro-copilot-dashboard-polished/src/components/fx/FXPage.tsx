import { Clock4, RefreshCw } from 'lucide-react';
import { useFxData } from '@/hooks/useFxData';

function fmtPct(value: number | null) {
  if (value === null || Number.isNaN(value)) return '—';
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function fmtNum(value: number | null, digits = 4) {
  if (value === null || Number.isNaN(value)) return '—';
  return value.toFixed(digits);
}

export function FXPage() {
  const { data, isLoading, error, refetch } = useFxData();

  if (error && !data) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center px-6">
        <div className="card max-w-md px-6 py-6">
          <p className="kicker mb-2">System</p>
          <p className="text-[15px] font-semibold text-fg-primary">
            FX data failed to load
          </p>
          <p className="mt-2 text-[12px] text-fg-secondary">{error.message}</p>
          <button onClick={refetch} className="btn-ghost mt-4 h-9">
            <RefreshCw size={13} />
            <span>Retry</span>
          </button>
        </div>
      </div>
    );
  }

  if (isLoading && !data) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto px-8 py-7 text-fg-secondary">
        Loading FX monitor…
      </div>
    );
  }

  if (!data) return null;

  const eurusd = data.eurusd.current_metrics;
  const asOfFormatted = eurusd.as_of_date
    ? new Date(eurusd.as_of_date + 'T00:00:00').toLocaleDateString('en-US', {
        weekday: 'long',
        month: 'short',
        day: 'numeric',
        year: 'numeric',
      })
    : '';

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-[1680px] px-6 py-6 lg:px-8 lg:py-7">
        <div className="mb-6 flex items-end justify-between gap-6">
          <div>
            <div className="flex items-center gap-2 text-fg-muted">
              <Clock4 size={11} />
              <span className="text-[11px] font-medium uppercase tracking-[0.14em]">
                {asOfFormatted} · FX Spot Monitor
              </span>
            </div>
            <h1 className="mt-2 text-[22px] font-semibold tracking-[-0.018em] text-fg-primary">
              FX Agent Monitor
            </h1>
            <p className="mt-1 text-[12.5px] text-fg-secondary">
              Deterministic FX analytics from TimescaleDB: spot levels, changes and 252-day z-scores.
            </p>
          </div>

          <button onClick={refetch} className="btn-ghost h-9">
            <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
            <span>Refresh</span>
          </button>
        </div>

        <div className="grid grid-cols-12 gap-4 lg:gap-5">
          <div className="card col-span-12 px-5 py-5 xl:col-span-4">
            <p className="kicker mb-2">Spot snapshot</p>
            <div className="flex items-baseline justify-between">
              <h2 className="text-[18px] font-semibold text-fg-primary">
                {eurusd.pair}
              </h2>
              <span className="text-[13px] text-fg-muted">{eurusd.as_of_date}</span>
            </div>

            <div className="mt-5">
              <div className="text-[34px] font-semibold tracking-[-0.04em] text-fg-primary">
                {fmtNum(eurusd.current_spot)}
              </div>
              <div className="mt-3 grid grid-cols-3 gap-3 text-[12px]">
                <Metric label="1D" value={fmtPct(eurusd.daily_change_pct)} />
                <Metric label="1W" value={fmtPct(eurusd.weekly_change_pct)} />
                <Metric label="1M" value={fmtPct(eurusd.monthly_change_pct)} />
              </div>
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3 text-[12px]">
              <Metric label="Z-score" value={fmtNum(eurusd.z_score, 2)} />
              <Metric label="252d pctile" value={fmtNum(eurusd.percentile_252d, 1)} />
              <Metric label="252d high" value={fmtNum(eurusd.high_252d)} />
              <Metric label="252d low" value={fmtNum(eurusd.low_252d)} />
            </div>
          </div>

          <div className="card col-span-12 px-5 py-5 xl:col-span-8">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <p className="kicker mb-2">Scanner</p>
                <h2 className="text-[15px] font-semibold text-fg-primary">
                  G10 FX stretched pairs
                </h2>
              </div>
              <span className="text-[11px] text-fg-muted">
                ranked by |z-score|
              </span>
            </div>

            <div className="overflow-hidden rounded-xl border border-line-soft">
              <table className="w-full text-left text-[12px]">
                <thead className="bg-white/[0.025] text-fg-muted">
                  <tr>
                    <Th>Pair</Th>
                    <Th>Spot</Th>
                    <Th>1D</Th>
                    <Th>1W</Th>
                    <Th>1M</Th>
                    <Th>3M</Th>
                    <Th>Z</Th>
                    <Th>Signal</Th>
                    <Th>As of</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.scanner.rows.map((row) => (
                    <tr key={row.pair} className="border-t border-line-subtle">
                      <Td strong>{row.pair}</Td>
                      <Td>{fmtNum(row.current_spot, row.pair.includes('JPY') ? 2 : 4)}</Td>
                      <Td>{fmtPct(row.daily_change_pct)}</Td>
                      <Td>{fmtPct(row.weekly_change_pct)}</Td>
                      <Td>{fmtPct(row.momentum_1m_pct)}</Td>
                      <Td>{fmtPct(row.momentum_3m_pct)}</Td>
                      <Td>{fmtNum(row.z_score, 2)}</Td>
                      <Td>{row.signal}</Td>
                      <Td muted>{row.as_of_date}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line-soft bg-white/[0.02] px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.12em] text-fg-muted">{label}</div>
      <div className="mt-1 font-medium text-fg-primary">{value}</div>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-3 py-2 font-medium">{children}</th>;
}

function Td({
  children,
  strong,
  muted,
}: {
  children: React.ReactNode;
  strong?: boolean;
  muted?: boolean;
}) {
  return (
    <td className={`px-3 py-2 ${strong ? 'font-semibold text-fg-primary' : muted ? 'text-fg-muted' : 'text-fg-secondary'}`}>
      {children}
    </td>
  );
}