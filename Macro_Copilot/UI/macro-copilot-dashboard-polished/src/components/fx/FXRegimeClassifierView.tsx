import type { FXRegimeClassifierResponse } from '@/types/fx';

interface FXRegimeClassifierViewProps {
  payload: FXRegimeClassifierResponse;
}

const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

function scoreTone(score: number) {
  if (score > 0.35) return 'text-emerald-200';
  if (score < -0.35) return 'text-coral-200';
  return 'text-fg-secondary';
}

function labelTone(label: string) {
  const lower = label.toLowerCase();
  if (
    lower.includes('risk-on') ||
    lower.includes('weakness') ||
    lower.includes('friendly') ||
    lower.includes('cheap')
  ) {
    return 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200';
  }
  if (
    lower.includes('risk-off') ||
    lower.includes('strength') ||
    lower.includes('unattractive') ||
    lower.includes('rich')
  ) {
    return 'border-coral-300/25 bg-coral-300/10 text-coral-200';
  }
  return 'border-border-subtle bg-bg-secondary text-fg-secondary';
}

const confidenceTone: Record<FXRegimeClassifierResponse['confidence'], string> = {
  high: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200',
  medium: 'border-amber-300/25 bg-amber-300/10 text-amber-200',
  low: 'border-border-subtle bg-bg-secondary text-fg-secondary',
};

export default function FXRegimeClassifierView({
  payload,
}: FXRegimeClassifierViewProps) {
  return (
    <div className="px-6 py-6">
      <div className="grid gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
        <div className="space-y-4">
          <div className="card px-5 py-5">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
                  {payload.anchor_pair}
                </div>
                <div className="mt-2 text-[24px] font-semibold capitalize leading-tight text-fg-primary">
                  {payload.overall_regime}
                </div>
              </div>
              <div
                className={`shrink-0 rounded-md border px-2.5 py-1 text-[11px] font-semibold capitalize ${
                  confidenceTone[payload.confidence]
                }`}
              >
                {payload.confidence}
              </div>
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <Metric
                label="Total score"
                value={`${payload.total_score > 0 ? '+' : ''}${formatNumber(payload.total_score, 2)}`}
              />
              <Metric label="As of" value={payload.as_of_date ?? 'n/a'} />
              <Metric label="USD" value={payload.usd_regime} />
              <Metric label="Carry" value={payload.carry_regime} />
            </div>
          </div>

          <DetailPanel title="Drivers" rows={payload.drivers} empty="No positive drivers flagged." />
          <DetailPanel title="Risks" rows={payload.risks} empty="No major regime risks flagged." />
        </div>

        <div className="space-y-4">
          <div className="card overflow-hidden">
            <div className="border-b border-border-subtle px-5 py-4">
              <div className="text-[13px] font-semibold text-fg-primary">
                Regime Components
              </div>
              <div className="mt-1 text-[11.5px] text-fg-muted">
                Deterministic blend of USD pressure, macro risk, volatility premium and carry availability.
              </div>
            </div>

            <div className="divide-y divide-border-subtle">
              {payload.components.map((component) => (
                <div
                  key={component.name}
                  className="grid gap-3 px-5 py-4 md:grid-cols-[120px_180px_80px_minmax(0,1fr)]"
                >
                  <div className="text-[12px] font-semibold text-fg-primary">
                    {component.name}
                  </div>
                  <div>
                    <span
                      className={`inline-flex rounded-md border px-2 py-1 text-[11px] font-semibold capitalize ${labelTone(
                        component.label,
                      )}`}
                    >
                      {component.label}
                    </span>
                  </div>
                  <div className={`mono text-[13px] font-semibold ${scoreTone(component.score)}`}>
                    {component.score > 0 ? '+' : ''}
                    {formatNumber(component.score, 2)}
                  </div>
                  <div className="text-[12px] leading-5 text-fg-secondary">
                    {component.summary}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <DetailPanel
            title="Follow-Ups"
            rows={payload.follow_ups}
            empty="No follow-ups suggested."
          />
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[8px] border border-border-subtle bg-bg-secondary px-3 py-3">
      <div className="text-[11px] text-fg-muted">{label}</div>
      <div className="mt-1 text-[13px] font-semibold capitalize text-fg-primary">
        {value}
      </div>
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

