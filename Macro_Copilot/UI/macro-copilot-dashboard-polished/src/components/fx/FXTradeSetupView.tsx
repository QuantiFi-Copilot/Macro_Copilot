import type { FXTradeSetupResponse } from '@/types/fx';

interface FXTradeSetupViewProps {
  payload: FXTradeSetupResponse;
}

const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

const stanceClass: Record<string, string> = {
  bullish: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200',
  bearish: 'border-coral-300/25 bg-coral-300/10 text-coral-200',
  neutral: 'border-border-subtle bg-bg-secondary text-fg-secondary',
};

export default function FXTradeSetupView({ payload }: FXTradeSetupViewProps) {
  const spotDigits = payload.pair.includes('JPY') ? 2 : 4;

  return (
    <div className="px-6 py-6">
      <div className="grid gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
        <div className="space-y-4">
          <div className="card px-5 py-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
                  {payload.pair}
                </div>
                <div className="mt-2 text-[28px] font-semibold capitalize text-fg-primary">
                  {payload.direction}
                </div>
                <div className="mt-1 text-[12px] text-fg-secondary">
                  {payload.summary}
                </div>
              </div>
              <div
                className={`rounded-md border px-2.5 py-1 text-[11px] font-semibold capitalize ${
                  stanceClass[payload.direction]
                }`}
              >
                {payload.confidence}
              </div>
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <Metric label="Score" value={`${payload.total_score > 0 ? '+' : ''}${formatNumber(payload.total_score, 2)}`} />
              <Metric label="Tenor" value={payload.tenor} />
              <Metric label="Spot" value={formatNumber(payload.spot_snapshot.current_spot, spotDigits)} />
              <Metric
                label="Realized vol"
                value={`${formatNumber(payload.realized_vol_snapshot.realized_vol_annualized_pct, 2)}%`}
              />
            </div>
          </div>

          <div className="card px-5 py-4">
            <div className="text-[12px] font-semibold text-fg-primary">Risks</div>
            <div className="mt-3 space-y-2">
              {payload.risks.map((risk) => (
                <div key={risk} className="text-[12px] leading-5 text-fg-secondary">
                  {risk}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="space-y-4">
          <div className="card overflow-hidden">
            <div className="border-b border-border-subtle px-5 py-4">
              <div className="text-[13px] font-semibold text-fg-primary">
                Signal Stack
              </div>
              <div className="mt-1 text-[11.5px] text-fg-muted">
                Deterministic scoring from spot, carry, forwards and realized vol.
              </div>
            </div>

            <div className="divide-y divide-border-subtle">
              {payload.signals.map((signal) => (
                <div key={signal.name} className="grid gap-3 px-5 py-4 md:grid-cols-[180px_96px_minmax(0,1fr)]">
                  <div>
                    <div className="text-[12px] font-semibold text-fg-primary">{signal.name}</div>
                    <div className="mt-1 text-[11px] capitalize text-fg-muted">{signal.stance}</div>
                  </div>
                  <div className="mono text-[13px] font-semibold text-fg-primary">
                    {signal.score > 0 ? '+' : ''}{formatNumber(signal.score, 2)}
                  </div>
                  <div className="text-[12px] leading-5 text-fg-secondary">
                    {signal.description}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <DetailPanel title="Key Drivers" rows={payload.key_drivers} />
            <DetailPanel title="Follow-Ups" rows={payload.follow_up_questions} />
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

function DetailPanel({ title, rows }: { title: string; rows: string[] }) {
  return (
    <div className="card px-5 py-4">
      <div className="text-[12px] font-semibold text-fg-primary">{title}</div>
      <div className="mt-3 space-y-2">
        {rows.map((row) => (
          <div key={row} className="text-[12px] leading-5 text-fg-secondary">
            {row}
          </div>
        ))}
      </div>
    </div>
  );
}
