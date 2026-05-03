import type { FXSpotLevelResponse } from '@/types/fx';

interface FXSpotViewProps {
  payload: FXSpotLevelResponse;
}

const formatNumber = (value: number | null | undefined, digits = 4) =>
  value === null || value === undefined ? 'n/a' : value.toFixed(digits);

export default function FXSpotView({ payload }: FXSpotViewProps) {
  const metrics = payload.current_metrics;

  return (
    <div className="px-6 py-6">
      <div className="card px-5 py-4">
        <div className="text-[12px] font-semibold uppercase tracking-[0.18em] text-fg-muted">
          {metrics.pair}
        </div>
        <div className="mt-2 text-[28px] font-semibold text-fg-primary">
          {formatNumber(metrics.current_spot)}
        </div>
        <div className="mt-1 text-[12px] text-fg-secondary">
          As of {metrics.as_of_date}
        </div>

        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <Metric label="1D" value={`${formatNumber(metrics.daily_change_pct, 2)}%`} />
          <Metric label="1W" value={`${formatNumber(metrics.weekly_change_pct, 2)}%`} />
          <Metric label="1M" value={`${formatNumber(metrics.monthly_change_pct, 2)}%`} />
          <Metric label="Z-score" value={formatNumber(metrics.z_score, 2)} />
          <Metric label="252D high" value={formatNumber(metrics.high_252d)} />
          <Metric label="252D low" value={formatNumber(metrics.low_252d)} />
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
