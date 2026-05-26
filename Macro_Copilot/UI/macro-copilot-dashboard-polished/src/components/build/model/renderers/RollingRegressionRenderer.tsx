// ============================================================================
// RollingRegressionRenderer — bespoke render for calculate_rolling_regression
// ----------------------------------------------------------------------------
// The rolling_regression primitive's output dict includes:
//   - current_metrics: { target_label, regressor_labels[], current_alpha_pct,
//                        current_betas: {label: number}, current_residual_pct,
//                        current_r_squared, ... as_of_date }
//   - time_series_betas:        [{series_name, units, frequency, rows: [{date, value}]}]
//   - time_series_alpha:        TimeSeries
//   - time_series_residual:     TimeSeries
//   - time_series_r_squared:    TimeSeries
//   - time_series_condition_flag: TimeSeries
//
// Layout:
//   1. Snapshot KPIs (alpha, R², per-regressor beta).
//   2. Beta time series — one line per regressor.
//   3. R² ribbon under it.
//   4. Residual + alpha mini panels.
// ============================================================================

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { chartStrokeForTone, type ChartTone } from '@/lib/chart';
import { cn } from '@/utils/cn';

type RrOutput = Record<string, unknown>;

const REGRESSOR_TONES: ChartTone[] = ['blue', 'amber', 'rates', 'green', 'coral', 'neutral'];

type TsRow = { date: string; value: number | null };
type Ts = { series_name: string; units?: string; rows: TsRow[] };

export function RollingRegressionRenderer({ output }: { output: RrOutput }) {
  const m = (output['current_metrics'] ?? {}) as Record<string, unknown>;
  const targetLabel = (m['target_label'] ?? '?') as string;
  const regressorLabels = (m['regressor_labels'] ?? []) as string[];
  const currentAlpha = numOrNull(m['current_alpha_pct']);
  const currentBetas = (m['current_betas'] ?? {}) as Record<string, number | null>;
  const currentResidual = numOrNull(m['current_residual_pct']);
  const currentR2 = numOrNull(m['current_r_squared']);
  const asOf = (m['as_of_date'] ?? null) as string | null;

  const tsBetas = (output['time_series_betas'] ?? []) as Ts[];
  const tsAlpha = output['time_series_alpha'] as Ts | undefined;
  const tsR2 = output['time_series_r_squared'] as Ts | undefined;
  const tsResidual = output['time_series_residual'] as Ts | undefined;

  return (
    <div className="space-y-5">
      <SnapshotStrip
        targetLabel={targetLabel}
        regressorLabels={regressorLabels}
        currentBetas={currentBetas}
        currentAlpha={currentAlpha}
        currentResidual={currentResidual}
        currentR2={currentR2}
        asOf={asOf}
      />

      <BetaSeriesChart
        tsBetas={tsBetas}
        regressorLabels={regressorLabels}
      />

      {tsR2 ? <RsquaredRibbon ts={tsR2} /> : null}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {tsAlpha ? <SingleSeriesCard title="Alpha (yield-percent)" ts={tsAlpha} tone="rates" /> : null}
        {tsResidual ? <SingleSeriesCard title="Residual (yield-percent)" ts={tsResidual} tone="amber" /> : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function SnapshotStrip({
  targetLabel,
  regressorLabels,
  currentBetas,
  currentAlpha,
  currentResidual,
  currentR2,
  asOf,
}: {
  targetLabel: string;
  regressorLabels: string[];
  currentBetas: Record<string, number | null>;
  currentAlpha: number | null;
  currentResidual: number | null;
  currentR2: number | null;
  asOf: string | null;
}) {
  return (
    <section className="card px-5 py-4">
      <div className="flex items-baseline justify-between">
        <div className="kicker">
          {targetLabel} ~ {regressorLabels.join(' + ')}
        </div>
        {asOf ? (
          <span className="mono text-[10.5px] text-fg-faint">as of {asOf}</span>
        ) : null}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="R²" value={currentR2 != null ? currentR2.toFixed(3) : '—'} emphasis />
        <Tile label="Alpha" value={currentAlpha != null ? currentAlpha.toFixed(4) : '—'} unit="%" />
        <Tile label="Residual" value={currentResidual != null ? currentResidual.toFixed(4) : '—'} unit="%" />
        <Tile
          label={regressorLabels.length === 1 ? `β ${regressorLabels[0]}` : 'βs'}
          value={
            regressorLabels.length === 1
              ? formatBeta(currentBetas[regressorLabels[0]])
              : '— see below —'
          }
          small={regressorLabels.length !== 1}
        />
      </div>

      {regressorLabels.length > 1 ? (
        <div className="mt-3 border-t border-line-subtle pt-3">
          <p className="mb-1.5 text-[10px] uppercase tracking-[0.12em] text-fg-faint">
            Per-regressor β · current
          </p>
          <div className="flex flex-wrap gap-2">
            {regressorLabels.map((label, i) => {
              const tone = REGRESSOR_TONES[i % REGRESSOR_TONES.length];
              return (
                <span
                  key={label}
                  className="flex items-center gap-2 rounded-md border border-line-soft bg-white/[0.012] px-2.5 py-1 text-[11px]"
                >
                  <span
                    aria-hidden
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: chartStrokeForTone(tone) }}
                  />
                  <span className="mono text-fg-secondary">{label}</span>
                  <span className="mono text-fg-primary">
                    {formatBeta(currentBetas[label])}
                  </span>
                </span>
              );
            })}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function Tile({
  label,
  value,
  unit,
  emphasis,
  small,
}: {
  label: string;
  value: string;
  unit?: string;
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
        {unit ? <span className="ml-1 text-[10px] text-fg-faint">{unit}</span> : null}
      </p>
    </div>
  );
}

function formatBeta(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return v.toFixed(3);
}

// ---------------------------------------------------------------------------

function BetaSeriesChart({
  tsBetas,
  regressorLabels,
}: {
  tsBetas: Ts[];
  regressorLabels: string[];
}) {
  if (tsBetas.length === 0) return null;

  // Wide rows keyed by date.
  const allDates = new Set<string>();
  for (const ts of tsBetas) for (const r of ts.rows) allDates.add(r.date);
  const sortedDates = [...allDates].sort();

  const wide = sortedDates.map((d) => {
    const row: Record<string, string | number | null> = { date: d };
    for (let i = 0; i < tsBetas.length; i++) {
      const label = regressorLabels[i] ?? tsBetas[i].series_name;
      const match = tsBetas[i].rows.find((r) => r.date === d);
      row[label] = match?.value ?? null;
    }
    return row;
  });

  return (
    <section className="card overflow-hidden">
      <div className="flex items-baseline justify-between border-b border-line-subtle px-5 py-3">
        <div>
          <div className="kicker">Rolling β · per regressor</div>
          <p className="mt-0.5 text-[10.5px] text-fg-muted">
            Trailing-window OLS coefficient for each regressor at each
            window-end date. Reference line at β=1 (one-for-one).
          </p>
        </div>
      </div>
      <div className="px-3 py-3">
        <ResponsiveContainer width="100%" height={320}>
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
            <ReferenceLine y={1} stroke="rgba(148,163,184,0.25)" strokeDasharray="2 4" />
            <ReferenceLine y={0} stroke="rgba(148,163,184,0.15)" />
            {regressorLabels.map((label, i) => {
              const tone = REGRESSOR_TONES[i % REGRESSOR_TONES.length];
              return (
                <Line
                  key={label}
                  type="monotone"
                  dataKey={label}
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

function RsquaredRibbon({ ts }: { ts: Ts }) {
  return (
    <section className="card overflow-hidden">
      <div className="flex items-baseline justify-between border-b border-line-subtle px-5 py-3">
        <div>
          <div className="kicker">Rolling R² · in-window fit quality</div>
          <p className="mt-0.5 text-[10.5px] text-fg-muted">
            Drop in R² → linear hedge ratio losing predictive power; possible
            structural break.
          </p>
        </div>
      </div>
      <div className="px-3 py-3">
        <ResponsiveContainer width="100%" height={140}>
          <LineChart data={ts.rows.map((r) => ({ date: r.date, value: r.value }))} margin={{ top: 5, right: 18, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
            <XAxis
              dataKey="date"
              tickFormatter={(v) => formatDate(v as string)}
              tick={{ fill: 'rgba(148,163,184,0.6)', fontSize: 10 }}
              minTickGap={50}
            />
            <YAxis
              domain={[0, 1]}
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
            <Line
              type="monotone"
              dataKey="value"
              stroke={chartStrokeForTone('green')}
              strokeWidth={1.4}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function SingleSeriesCard({
  title,
  ts,
  tone,
}: {
  title: string;
  ts: Ts;
  tone: ChartTone;
}) {
  return (
    <section className="card overflow-hidden">
      <div className="border-b border-line-subtle px-5 py-3">
        <div className="kicker">{title}</div>
      </div>
      <div className="px-3 py-3">
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={ts.rows.map((r) => ({ date: r.date, value: r.value }))} margin={{ top: 5, right: 18, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.08)" />
            <XAxis
              dataKey="date"
              tickFormatter={(v) => formatDate(v as string)}
              tick={{ fill: 'rgba(148,163,184,0.6)', fontSize: 10 }}
              minTickGap={50}
            />
            <YAxis tick={{ fill: 'rgba(148,163,184,0.6)', fontSize: 10 }} width={42} />
            <Tooltip
              contentStyle={{
                backgroundColor: 'rgba(12,14,21,0.96)',
                border: '1px solid rgba(148,163,184,0.18)',
                borderRadius: 8,
                fontSize: 11,
                color: '#E8EAF0',
              }}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke={chartStrokeForTone(tone)}
              strokeWidth={1.4}
              dot={false}
              isAnimationActive={false}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------

function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });
}
