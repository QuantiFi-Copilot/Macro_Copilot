// ============================================================================
// PcaLoadingsRenderer — bespoke render for calculate_pca_yield_curve_tool
// ----------------------------------------------------------------------------
// The PCA primitive's output dict includes:
//   - current_metrics.loadings:                 [{tenor, pc1, pc2, ...}]
//   - current_metrics.variance_explained:       [{component_name, variance_share, cumulative_share}]
//   - current_metrics.current_factor_levels:    {pc1: ..., pc2: ..., ...}
//   - current_metrics.component_metadata:       [{component_name, quality_flag, ...}]
//   - time_series_factors:                       [{series_name, units, frequency, rows: [{date, value}]}]
//
// Layout:
//   1. Headline strip — n_components, total_variance_explained, observation_count
//   2. Loadings panel — table view + heatmap
//   3. Variance-explained bars — horizontal stacked bars per component
//   4. Factor scores time series — multi-line chart over time_series_factors
// ============================================================================

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { chartStrokeForTone, type ChartTone } from '@/lib/chart';
import { cn } from '@/utils/cn';

type PcaOutput = Record<string, unknown>;

const PC_TONES: ChartTone[] = ['blue', 'rates', 'amber', 'green', 'coral', 'neutral'];

export function PcaLoadingsRenderer({ output }: { output: PcaOutput }) {
  const metrics = (output['current_metrics'] ?? {}) as Record<string, unknown>;
  const tenors = (metrics['tenors_used'] ?? []) as string[];
  const loadings = (metrics['loadings'] ?? []) as Array<Record<string, unknown>>;
  const variance = (metrics['variance_explained'] ?? []) as Array<{
    component_name: string;
    variance_share: number;
    cumulative_share: number;
  }>;
  const currentFactorLevels = (metrics['current_factor_levels'] ?? {}) as Record<
    string,
    number | null
  >;
  const totalVar = (metrics['total_variance_explained'] ?? null) as
    | number
    | null;
  const obsCount = (metrics['observation_count'] ?? null) as number | null;
  const nComponents = (metrics['n_components_returned'] ?? null) as
    | number
    | null;
  const fitStart = (metrics['fit_window_start'] ?? null) as string | null;
  const fitEnd = (metrics['fit_window_end'] ?? null) as string | null;

  const componentNames = variance.map((v) => v.component_name);
  const tsFactors = (output['time_series_factors'] ?? []) as Array<{
    series_name: string;
    units: string;
    rows: Array<{ date: string; value: number | null }>;
  }>;

  return (
    <div className="space-y-5">
      <HeadlineStrip
        nComponents={nComponents}
        totalVar={totalVar}
        obsCount={obsCount}
        fitStart={fitStart}
        fitEnd={fitEnd}
      />

      <LoadingsCard
        tenors={tenors}
        loadings={loadings}
        componentNames={componentNames}
      />

      <VarianceExplainedCard variance={variance} />

      {tsFactors.length > 0 ? (
        <FactorScoresChart
          factors={tsFactors}
          componentNames={componentNames}
          currentLevels={currentFactorLevels}
        />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------

function HeadlineStrip({
  nComponents,
  totalVar,
  obsCount,
  fitStart,
  fitEnd,
}: {
  nComponents: number | null;
  totalVar: number | null;
  obsCount: number | null;
  fitStart: string | null;
  fitEnd: string | null;
}) {
  return (
    <section className="card grid grid-cols-2 gap-3 px-5 py-4 md:grid-cols-4">
      <Tile label="Components" value={nComponents != null ? String(nComponents) : '—'} />
      <Tile
        label="Variance explained"
        value={
          totalVar != null && Number.isFinite(totalVar)
            ? `${(totalVar * 100).toFixed(1)}%`
            : '—'
        }
        emphasis
      />
      <Tile label="Observations" value={obsCount != null ? String(obsCount) : '—'} />
      <Tile
        label="Fit window"
        value={fitStart && fitEnd ? `${fitStart} → ${fitEnd}` : '—'}
        small
      />
    </section>
  );
}

function Tile({
  label,
  value,
  emphasis,
  small,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
  small?: boolean;
}) {
  return (
    <div>
      <p className="text-[9.5px] uppercase tracking-[0.14em] text-fg-faint">
        {label}
      </p>
      <p
        className={cn(
          'mono mt-1 text-fg-primary',
          emphasis ? 'text-[19px] font-semibold' : 'text-[14.5px]',
          small && 'text-[11.5px] text-fg-secondary',
        )}
      >
        {value}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loadings — table + simple heatmap-style background per cell
// ---------------------------------------------------------------------------

function LoadingsCard({
  tenors,
  loadings,
  componentNames,
}: {
  tenors: string[];
  loadings: Array<Record<string, unknown>>;
  componentNames: string[];
}) {
  // Compute the absolute max loading for cell-tone scaling.
  let absMax = 0;
  for (const row of loadings) {
    for (const k of componentNames) {
      const v = row[k];
      if (typeof v === 'number' && Number.isFinite(v)) {
        absMax = Math.max(absMax, Math.abs(v));
      }
    }
  }
  const denom = absMax === 0 ? 1 : absMax;

  return (
    <section className="card overflow-hidden">
      <div className="flex items-baseline justify-between border-b border-line-subtle px-5 py-3">
        <div>
          <div className="kicker">Loadings</div>
          <p className="mt-0.5 text-[10.5px] text-fg-muted">
            One row per tenor; one column per component. Bar shading shows
            magnitude; mint = positive, coral = negative.
          </p>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="mono w-full min-w-[420px] text-[11px] tabular-nums">
          <thead>
            <tr className="border-b border-line-subtle text-[10px] uppercase tracking-[0.06em] text-fg-faint">
              <th className="px-4 py-2 text-left font-normal">Tenor</th>
              {componentNames.map((n) => (
                <th key={n} className="px-4 py-2 text-right font-normal">
                  {n}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loadings.map((row, ri) => (
              <tr
                key={ri}
                className="border-b border-line-subtle/40 transition-colors hover:bg-white/[0.012]"
              >
                <td className="px-4 py-2 text-fg-primary">
                  {(row['tenor'] ?? tenors[ri] ?? `#${ri}`) as string}
                </td>
                {componentNames.map((n) => {
                  const v = row[n];
                  const finite = typeof v === 'number' && Number.isFinite(v);
                  const num = finite ? (v as number) : 0;
                  const ratio = (num / denom) * 100;
                  const color = num >= 0 ? 'mint' : 'coral';
                  return (
                    <td key={n} className="relative px-4 py-2 text-right">
                      <div
                        aria-hidden
                        className={cn(
                          'absolute top-1/2 h-[60%] -translate-y-1/2 rounded-[2px]',
                          color === 'mint'
                            ? 'left-1/2 bg-mint-400/15'
                            : 'right-1/2 bg-coral-400/15',
                        )}
                        style={{ width: `${Math.abs(ratio) / 2}%` }}
                      />
                      <span
                        className={cn(
                          'relative',
                          finite ? 'text-fg-primary' : 'text-fg-faint',
                        )}
                      >
                        {finite ? (v as number).toFixed(3) : '—'}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Variance-explained — horizontal bar per component + cumulative line
// ---------------------------------------------------------------------------

function VarianceExplainedCard({
  variance,
}: {
  variance: Array<{
    component_name: string;
    variance_share: number;
    cumulative_share: number;
  }>;
}) {
  if (variance.length === 0) return null;
  return (
    <section className="card px-5 py-4">
      <div className="kicker mb-2.5">Variance explained</div>
      <div className="space-y-2">
        {variance.map((v, i) => {
          const tone = PC_TONES[i % PC_TONES.length];
          const stroke = chartStrokeForTone(tone);
          const pctShare = v.variance_share * 100;
          const pctCum = v.cumulative_share * 100;
          return (
            <div key={v.component_name} className="flex items-center gap-3">
              <span className="mono w-10 shrink-0 text-[10.5px] text-fg-secondary">
                {v.component_name}
              </span>
              <div className="relative flex-1 overflow-hidden rounded-md border border-line-subtle bg-white/[0.012]">
                <div
                  className="h-3"
                  style={{ width: `${pctShare}%`, backgroundColor: stroke + '88' }}
                />
              </div>
              <span className="mono w-14 shrink-0 text-right text-[10.5px] text-fg-primary">
                {pctShare.toFixed(1)}%
              </span>
              <span className="mono w-14 shrink-0 text-right text-[10.5px] text-fg-muted">
                Σ {pctCum.toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Factor scores time series — multi-line chart
// ---------------------------------------------------------------------------

function FactorScoresChart({
  factors,
  componentNames,
  currentLevels,
}: {
  factors: Array<{
    series_name: string;
    units: string;
    rows: Array<{ date: string; value: number | null }>;
  }>;
  componentNames: string[];
  currentLevels: Record<string, number | null>;
}) {
  // Build a single wide rows array, one row per date, columns = pc1, pc2 ...
  // Names from the primitive look like
  // "ust_pc1_factor_daily" — extract the pcN by splitting.
  const seriesByPC: Record<string, Array<{ date: string; value: number | null }>> = {};
  for (const f of factors) {
    const m = f.series_name.match(/_(pc\d+)_/);
    const key = m ? m[1] : f.series_name;
    seriesByPC[key] = f.rows;
  }

  const allDates = new Set<string>();
  for (const rows of Object.values(seriesByPC)) {
    for (const r of rows) allDates.add(r.date);
  }
  const sortedDates = [...allDates].sort();

  type WideRow = Record<string, string | number | null>;
  const wide: WideRow[] = sortedDates.map((d) => {
    const row: WideRow = { date: d };
    for (const k of Object.keys(seriesByPC)) {
      const match = seriesByPC[k].find((r) => r.date === d);
      row[k] = match?.value ?? null;
    }
    return row;
  });

  if (wide.length === 0) return null;

  return (
    <section className="card overflow-hidden">
      <div className="flex items-baseline justify-between border-b border-line-subtle px-5 py-3">
        <div>
          <div className="kicker">Factor scores · over time</div>
          <p className="mt-0.5 text-[10.5px] text-fg-muted">
            Each component's daily score on the centered yield-changes panel.
          </p>
        </div>
        <div className="flex items-baseline gap-3 text-[10.5px]">
          {componentNames.map((n, i) => {
            const tone = PC_TONES[i % PC_TONES.length];
            const stroke = chartStrokeForTone(tone);
            const v = currentLevels[n];
            return (
              <span key={n} className="flex items-center gap-1.5">
                <span
                  aria-hidden
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: stroke }}
                />
                <span className="mono text-fg-secondary">{n}</span>
                <span className="mono text-fg-faint">
                  {typeof v === 'number' ? v.toFixed(2) : '—'}
                </span>
              </span>
            );
          })}
        </div>
      </div>
      <div className="px-3 py-3">
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={wide} margin={{ top: 5, right: 18, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
            <XAxis
              dataKey="date"
              tickFormatter={(v) => formatDate(v as string)}
              tick={{ fill: 'rgba(148,163,184,0.6)', fontSize: 10 }}
              minTickGap={50}
            />
            <YAxis
              tick={{ fill: 'rgba(148,163,184,0.6)', fontSize: 10 }}
              width={42}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: 'rgba(12,14,21,0.96)',
                border: '1px solid rgba(148,163,184,0.18)',
                borderRadius: 8,
                fontSize: 11,
                color: '#E8EAF0',
              }}
            />
            {componentNames.map((n, i) => {
              const tone = PC_TONES[i % PC_TONES.length];
              return (
                <Line
                  key={n}
                  type="monotone"
                  dataKey={n}
                  stroke={chartStrokeForTone(tone)}
                  strokeWidth={1.4}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              );
            })}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });
}
