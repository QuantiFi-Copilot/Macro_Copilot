import type {
  FXCurrencyThesisExpression,
  FXCurrencyThesisMetric,
  FXCurrencyThesisResponse,
} from '@/types/fx';

interface FXCurrencyThesisMonitorViewProps {
  payload: FXCurrencyThesisResponse;
}

const formatNumber = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

const formatSigned = (value: number | null | undefined, digits = 2) => {
  if (value === null || value === undefined) return 'n/a';
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}`;
};

const statusTone: Record<FXCurrencyThesisResponse['thesis_status'], string> = {
  supportive: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200',
  mixed: 'border-amber-300/25 bg-amber-300/10 text-amber-200',
  hostile: 'border-coral-300/25 bg-coral-300/10 text-coral-200',
};

const confidenceTone: Record<FXCurrencyThesisResponse['confidence'], string> = {
  high: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200',
  medium: 'border-amber-300/25 bg-amber-300/10 text-amber-200',
  low: 'border-border-subtle bg-bg-secondary text-fg-secondary',
};

const metricTone: Record<FXCurrencyThesisMetric['status'], string> = {
  confirming: 'text-emerald-200',
  challenging: 'text-coral-200',
  neutral: 'text-fg-secondary',
};

export default function FXCurrencyThesisMonitorView({
  payload,
}: FXCurrencyThesisMonitorViewProps) {
  return (
    <div className="px-6 py-6">
      <div className="grid gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
        <div className="space-y-4">
          <div className="card px-5 py-5">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
                  {payload.view} {payload.currency}
                </div>
                <div className="mt-2 text-[24px] font-semibold capitalize leading-tight text-fg-primary">
                  {payload.thesis_status}
                </div>
              </div>
              <div
                className={`shrink-0 rounded-md border px-2.5 py-1 text-[11px] font-semibold capitalize ${
                  statusTone[payload.thesis_status]
                }`}
              >
                {payload.thesis_status}
              </div>
            </div>

            <div className="mt-4 text-[12px] leading-5 text-fg-secondary">
              {payload.summary}
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <Metric label="Score" value={formatSigned(payload.confirmation_score, 2)} />
              <Metric label="Pressure" value={`${formatSigned(payload.currency_pressure_score_pct, 2)}%`} />
              <Metric
                label="Rank"
                value={payload.currency_rank ? `#${payload.currency_rank}` : 'n/a'}
              />
              <Metric label="As of" value={payload.as_of_date ?? 'n/a'} />
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              <Badge label={`Confidence ${payload.confidence}`} tone={confidenceTone[payload.confidence]} />
              <Badge label={`Strongest ${payload.strongest_currency ?? 'n/a'}`} />
              <Badge label={`Weakest ${payload.weakest_currency ?? 'n/a'}`} />
            </div>
          </div>

          <DetailPanel
            title="Confirmations"
            rows={payload.confirmations}
            empty="No confirmations currently flagged."
          />
          <DetailPanel
            title="Challenges"
            rows={payload.challenges}
            empty="No challenges currently flagged."
          />
        </div>

        <div className="space-y-4">
          <ExpressionTable
            title="Best Expressions"
            rows={payload.best_expressions}
            empty="No clean expression candidates found."
          />

          <ExpressionTable
            title="Stretched Counter-Moves"
            rows={payload.stretched_counter_moves}
            empty="No stretched counter-moves flagged."
          />

          <div className="grid gap-4 lg:grid-cols-2">
            <MetricsPanel rows={payload.metrics_to_watch} />
            <DetailPanel
              title="Invalidation Signals"
              rows={payload.invalidation_signals}
              empty="No invalidation levels suggested."
            />
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

function Badge({ label, tone }: { label: string; tone?: string }) {
  return (
    <span
      className={`inline-flex rounded-md border px-2 py-1 text-[11px] font-semibold capitalize ${
        tone ?? 'border-border-subtle bg-bg-secondary text-fg-secondary'
      }`}
    >
      {label}
    </span>
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

function ExpressionTable({
  title,
  rows,
  empty,
}: {
  title: string;
  rows: FXCurrencyThesisExpression[];
  empty: string;
}) {
  return (
    <div className="card overflow-hidden">
      <div className="border-b border-border-subtle px-5 py-4">
        <div className="text-[13px] font-semibold text-fg-primary">{title}</div>
        <div className="mt-1 text-[11.5px] text-fg-muted">
          Pair-level expressions ranked by contribution and stretch.
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="px-5 py-5 text-[12px] leading-5 text-fg-muted">{empty}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-[12px]">
            <thead className="border-b border-border-subtle text-[11px] uppercase tracking-[0.12em] text-fg-muted">
              <tr>
                <th className="px-5 py-3 font-semibold">Pair</th>
                <th className="px-4 py-3 font-semibold">Expression</th>
                <th className="px-4 py-3 text-right font-semibold">Contribution</th>
                <th className="px-4 py-3 text-right font-semibold">1M</th>
                <th className="px-4 py-3 text-right font-semibold">Z</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {rows.map((row) => (
                <tr key={`${title}-${row.pair}-${row.expression}`}>
                  <td className="px-5 py-3 font-semibold text-fg-primary">{row.pair}</td>
                  <td className="max-w-[340px] px-4 py-3 text-fg-secondary">
                    <div className="font-semibold text-fg-primary">{row.expression}</div>
                    <div className="mt-1 leading-5 text-fg-muted">{row.rationale}</div>
                  </td>
                  <td className="mono px-4 py-3 text-right text-fg-primary">
                    {formatSigned(row.currency_contribution_pct, 2)}%
                  </td>
                  <td className="mono px-4 py-3 text-right text-fg-secondary">
                    {formatSigned(row.monthly_change_pct, 2)}%
                  </td>
                  <td className="mono px-4 py-3 text-right text-fg-secondary">
                    {formatNumber(row.z_score, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function MetricsPanel({ rows }: { rows: FXCurrencyThesisMetric[] }) {
  return (
    <div className="card px-5 py-4">
      <div className="text-[12px] font-semibold text-fg-primary">Metrics To Watch</div>
      <div className="mt-3 space-y-3">
        {rows.length === 0 ? (
          <div className="text-[12px] leading-5 text-fg-muted">No watch metrics available.</div>
        ) : (
          rows.map((row) => (
            <div key={row.name} className="rounded-[8px] border border-border-subtle bg-bg-secondary px-3 py-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 text-[12px] font-semibold text-fg-primary">
                  {row.name}
                </div>
                <div className={`mono shrink-0 text-[12px] font-semibold ${metricTone[row.status]}`}>
                  {row.value}
                </div>
              </div>
              <div className="mt-1 text-[11.5px] leading-5 text-fg-muted">{row.detail}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
