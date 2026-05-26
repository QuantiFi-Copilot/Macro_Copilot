// ============================================================================
// AttributionRenderer — bespoke render for
//                       calculate_yield_change_attribution_pca_tool
// ----------------------------------------------------------------------------
// Output shape (from current_metrics):
//   - curve_family, target_tenor
//   - start_date_resolved, end_date_resolved (window provenance)
//   - total_change_bps                    (the move being decomposed)
//   - component_contributions: [{component_name, contribution_bps,
//                                 loading_at_target_tenor,
//                                 variance_share_in_fit_window,
//                                 quality_flag}]
//   - residual_bps                         (orthogonal complement)
//   - n_components_used
//   - lookback_days_for_fit, change_frequency_for_fit, ...
//
// Layout:
//   1. Headline tile — total_change_bps (the "answer").
//   2. Decomposition bar — stacked horizontal bar of per-component +
//      residual contributions, signed.
//   3. Component table — name, contribution, loading, variance share,
//      quality flag.
// ============================================================================

import { AlertCircle, CheckCircle2 } from 'lucide-react';
import { chartStrokeForTone, type ChartTone } from '@/lib/chart';
import { cn } from '@/utils/cn';

type Output = Record<string, unknown>;

const PC_TONES: ChartTone[] = ['blue', 'rates', 'amber', 'green', 'coral', 'neutral'];

type Contribution = {
  component_name: string;
  contribution_bps: number | null;
  loading_at_target_tenor: number | null;
  variance_share_in_fit_window: number;
  quality_flag: 'ok' | 'degenerate' | 'sign_anchor_tied';
};

export function AttributionRenderer({ output }: { output: Output }) {
  const m = (output['current_metrics'] ?? {}) as Record<string, unknown>;
  const total = numOrNull(m['total_change_bps']);
  const residual = numOrNull(m['residual_bps']);
  const curve = (m['curve_family'] ?? '?') as string;
  const tenor = (m['target_tenor'] ?? '?') as string;
  const startDate = (m['start_date_resolved'] ?? null) as string | null;
  const endDate = (m['end_date_resolved'] ?? null) as string | null;
  const startReq = (m['start_date_requested'] ?? null) as string | null;
  const endReq = (m['end_date_requested'] ?? null) as string | null;
  const nUsed = (m['n_components_used'] ?? null) as number | null;
  const contributions = (m['component_contributions'] ?? []) as Contribution[];

  return (
    <div className="space-y-5">
      <HeadlineCard
        curve={curve}
        tenor={tenor}
        total={total}
        residual={residual}
        startDate={startDate}
        endDate={endDate}
        startReq={startReq}
        endReq={endReq}
        nUsed={nUsed}
      />

      <DecompositionBar
        contributions={contributions}
        residual={residual}
        total={total}
      />

      <ContributionTable contributions={contributions} residual={residual} />
    </div>
  );
}

function HeadlineCard({
  curve,
  tenor,
  total,
  residual,
  startDate,
  endDate,
  startReq,
  endReq,
  nUsed,
}: {
  curve: string;
  tenor: string;
  total: number | null;
  residual: number | null;
  startDate: string | null;
  endDate: string | null;
  startReq: string | null;
  endReq: string | null;
  nUsed: number | null;
}) {
  const sign = total != null && total >= 0 ? '+' : '';
  const totalTone = total != null && total >= 0 ? 'mint' : 'coral';
  return (
    <section className="card px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <div className="kicker">{curve} · {tenor}</div>
          <p className="mt-1 text-[11px] text-fg-muted">
            {startDate} → {endDate}
            {startReq !== startDate || endReq !== endDate ? (
              <span className="ml-1 text-fg-faint">
                (requested {startReq} → {endReq})
              </span>
            ) : null}
          </p>
        </div>
        <div className="text-right">
          <p className="text-[10px] uppercase tracking-[0.14em] text-fg-faint">
            Total yield change
          </p>
          <p
            className={cn(
              'mono mt-1 text-[24px] font-semibold',
              totalTone === 'mint' ? 'text-mint-300' : 'text-coral-300',
            )}
          >
            {total != null ? `${sign}${total.toFixed(2)}` : '—'}
            <span className="ml-1 text-[12px] text-fg-faint">bps</span>
          </p>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 border-t border-line-subtle pt-3 md:grid-cols-3">
        <Tile label="Components" value={nUsed != null ? String(nUsed) : '—'} />
        <Tile
          label="Residual"
          value={residual != null ? `${residual >= 0 ? '+' : ''}${residual.toFixed(2)}` : '—'}
          unit="bps"
        />
        <Tile
          label="Decomp. quality"
          value={
            residual != null && Math.abs(residual) <= 5
              ? 'tight'
              : residual != null && Math.abs(residual) <= 15
                ? 'moderate'
                : 'wide'
          }
        />
      </div>
    </section>
  );
}

function Tile({
  label,
  value,
  unit,
}: {
  label: string;
  value: string;
  unit?: string;
}) {
  return (
    <div>
      <p className="text-[9.5px] uppercase tracking-[0.14em] text-fg-faint">
        {label}
      </p>
      <p className="mono mt-1 text-[14.5px] text-fg-primary">
        {value}
        {unit ? <span className="ml-1 text-[10px] text-fg-faint">{unit}</span> : null}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// DecompositionBar — diverging horizontal bars per contributor
// ---------------------------------------------------------------------------

function DecompositionBar({
  contributions,
  residual,
  total,
}: {
  contributions: Contribution[];
  residual: number | null;
  total: number | null;
}) {
  const rows = contributions.map((c, i) => {
    const v = c.contribution_bps;
    return {
      label: c.component_name,
      value: typeof v === 'number' && Number.isFinite(v) ? v : null,
      tone: PC_TONES[i % PC_TONES.length],
      quality: c.quality_flag,
    };
  });
  if (residual !== null) {
    rows.push({
      label: 'residual',
      value: residual,
      tone: 'neutral',
      quality: 'ok',
    });
  }

  const finite = rows
    .map((r) => r.value)
    .filter((v): v is number => typeof v === 'number');
  const absMax = finite.length > 0 ? Math.max(...finite.map(Math.abs)) : 1;
  const denom = absMax === 0 ? 1 : absMax;

  return (
    <section className="card overflow-hidden">
      <div className="flex items-baseline justify-between border-b border-line-subtle px-5 py-3">
        <div className="kicker">Decomposition · per component</div>
        {total !== null ? (
          <span className="mono text-[10.5px] text-fg-muted">
            target = Σ contributions + residual
          </span>
        ) : null}
      </div>
      <div className="space-y-2 px-5 py-4">
        {rows.map((r) => {
          const v = r.value ?? 0;
          const pct = (Math.abs(v) / denom) * 100;
          const positive = v >= 0;
          const stroke = chartStrokeForTone(r.tone);
          return (
            <div key={r.label} className="flex items-center gap-3 text-[11px]">
              <span className="mono w-20 shrink-0 text-fg-secondary">
                {r.label}
              </span>
              <div className="relative flex h-3.5 flex-1 items-center">
                <div className="absolute inset-y-0 left-1/2 w-px bg-line-subtle" />
                <div
                  className={cn(
                    'absolute h-2.5 rounded-[2px]',
                    positive ? 'left-1/2' : 'right-1/2',
                  )}
                  style={{
                    width: `${pct / 2}%`,
                    backgroundColor: stroke + '88',
                  }}
                />
              </div>
              <span
                className={cn(
                  'mono w-16 shrink-0 text-right',
                  v > 0 ? 'text-mint-300' : v < 0 ? 'text-coral-300' : 'text-fg-muted',
                )}
              >
                {r.value !== null
                  ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}`
                  : '—'}
              </span>
              <span className="mono shrink-0 text-[9.5px] text-fg-faint">
                bps
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Detailed contribution table
// ---------------------------------------------------------------------------

function ContributionTable({
  contributions,
  residual,
}: {
  contributions: Contribution[];
  residual: number | null;
}) {
  return (
    <section className="card overflow-hidden">
      <div className="border-b border-line-subtle px-5 py-3">
        <div className="kicker">Contributions · detail</div>
      </div>
      <div className="overflow-x-auto">
        <table className="mono w-full min-w-[560px] text-[11px] tabular-nums">
          <thead>
            <tr className="border-b border-line-subtle text-[10px] uppercase tracking-[0.06em] text-fg-faint">
              <th className="px-4 py-2 text-left font-normal">Component</th>
              <th className="px-4 py-2 text-right font-normal">Contribution (bps)</th>
              <th className="px-4 py-2 text-right font-normal">Loading at target</th>
              <th className="px-4 py-2 text-right font-normal">Variance share</th>
              <th className="px-4 py-2 text-left font-normal">Quality</th>
            </tr>
          </thead>
          <tbody>
            {contributions.map((c) => (
              <tr
                key={c.component_name}
                className="border-b border-line-subtle/40 transition-colors hover:bg-white/[0.012]"
              >
                <td className="px-4 py-2 text-fg-primary">{c.component_name}</td>
                <td
                  className={cn(
                    'px-4 py-2 text-right',
                    typeof c.contribution_bps === 'number'
                      ? c.contribution_bps >= 0
                        ? 'text-mint-300'
                        : 'text-coral-300'
                      : 'text-fg-muted',
                  )}
                >
                  {typeof c.contribution_bps === 'number'
                    ? `${c.contribution_bps >= 0 ? '+' : ''}${c.contribution_bps.toFixed(2)}`
                    : '—'}
                </td>
                <td className="px-4 py-2 text-right text-fg-secondary">
                  {typeof c.loading_at_target_tenor === 'number'
                    ? c.loading_at_target_tenor.toFixed(3)
                    : '—'}
                </td>
                <td className="px-4 py-2 text-right text-fg-secondary">
                  {(c.variance_share_in_fit_window * 100).toFixed(1)}%
                </td>
                <td className="px-4 py-2">
                  <QualityChip flag={c.quality_flag} />
                </td>
              </tr>
            ))}
            {residual !== null ? (
              <tr className="bg-white/[0.005]">
                <td className="px-4 py-2 text-fg-secondary italic">residual</td>
                <td
                  className={cn(
                    'px-4 py-2 text-right',
                    residual >= 0 ? 'text-mint-300/80' : 'text-coral-300/80',
                  )}
                >
                  {`${residual >= 0 ? '+' : ''}${residual.toFixed(2)}`}
                </td>
                <td className="px-4 py-2 text-right text-fg-faint">—</td>
                <td className="px-4 py-2 text-right text-fg-faint">—</td>
                <td className="px-4 py-2 text-fg-faint">orthogonal complement</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function QualityChip({
  flag,
}: {
  flag: 'ok' | 'degenerate' | 'sign_anchor_tied';
}) {
  if (flag === 'ok') {
    return (
      <span className="inline-flex items-center gap-1 rounded border border-mint-400/30 bg-mint-500/10 px-1.5 py-0.5 text-[9.5px] uppercase tracking-[0.06em] text-mint-300">
        <CheckCircle2 size={9} />
        ok
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded border border-amber-400/30 bg-amber-500/10 px-1.5 py-0.5 text-[9.5px] uppercase tracking-[0.06em] text-amber-300">
      <AlertCircle size={9} />
      {flag.replace(/_/g, ' ')}
    </span>
  );
}

function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}
