// ============================================================================
// PrimitiveModelView
// ----------------------------------------------------------------------------
// Generic, schema-driven workspace surface for any primitive registered with
// the rates_primitive_resolver.  Reads the tool's ToolCard via GET /tools/
// {name}, renders an interactive controls rail (one input per *Input field),
// fires POST /tools/{name}/run on Run, and renders the result inline:
//
//   - canonical TimeSeries fields (time_series, time_series_spread,
//     time_series_zscore, time_series_change_zscore, time_series_forward …)
//     → multi-series line chart + summary stat strip
//   - top-level scalar metrics (current_value, current_z_score, daily_change
//     …) → KPI tiles
//   - methodology + conventions surfaced in the rail's right column so the
//     PM can see "what knob ships out of the box, and why".
//
// One view component, every primitive.  As we register new primitives in
// rates_agent/workflows/__init__.py, they show up here automatically.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  Loader2,
  Play,
  Settings2,
  Sigma,
  SlidersHorizontal,
} from 'lucide-react';
import { useTool } from '@/hooks/useWorkflows';
import { runPrimitive } from '@/services/workflowsApi';
import type {
  PrimitiveRunResult,
  ToolCard,
  ToolFieldDescriptor,
} from '@/types/workflows';
import { cn } from '@/utils/cn';
import { WorkspaceChart, type WorkspaceChartPoint } from '../WorkspaceChart';
import { WorkspaceZScoreChart } from '../WorkspaceZScoreChart';
import {
  WorkspaceMetrics,
  formatSigned,
  toneForChange,
  toneForZScore,
  type MetricItem,
} from '../WorkspaceMetrics';

type PrimitiveModelViewProps = {
  /** Primitive tool_name as registered in rates_primitive_resolver. */
  toolName: string;
  /** Initial param overrides parsed from the URL.  These pre-populate the
   *  controls rail so a deep-link is fully reproducible. */
  initialParams: Record<string, string>;
};

// ---------------------------------------------------------------------------
// Param marshalling helpers
// ---------------------------------------------------------------------------

/** Best-effort coerce a URL-string into the type the backend expects. */
function coerceForType(raw: string, type: string): unknown {
  const t = (type || '').toLowerCase();
  if (raw === '') return undefined;
  if (t.includes('integer') || t === 'int') {
    const n = Number(raw);
    return Number.isFinite(n) ? Math.trunc(n) : raw;
  }
  if (t.includes('number') || t === 'float') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (t === 'boolean' || t === 'bool') {
    return raw === 'true' || raw === '1' || raw === 'yes';
  }
  return raw;
}

function defaultStringFor(field: ToolFieldDescriptor): string {
  if (field.default !== null && field.default !== undefined) {
    return String(field.default);
  }
  if (field.examples && field.examples.length > 0) {
    return String(field.examples[0]);
  }
  return '';
}

// Heuristic display order — put the obvious first ("symbol-like") inputs
// before lookback / window / threshold knobs so the rail reads naturally.
const PRIORITY_FIELD_ORDER = [
  'curve_family',
  'curve_family_1',
  'curve_family_2',
  'tenor',
  'short_tenor',
  'belly_tenor',
  'long_tenor',
  'forward_start',
  'forward_length',
  'lookback_days',
  'rolling_window_days',
];

function sortFields(fields: ToolFieldDescriptor[]): ToolFieldDescriptor[] {
  const idx = (name: string) => {
    const i = PRIORITY_FIELD_ORDER.indexOf(name);
    return i === -1 ? 999 : i;
  };
  return [...fields].sort((a, b) => {
    if (a.required !== b.required) return a.required ? -1 : 1;
    return idx(a.name) - idx(b.name);
  });
}

// ---------------------------------------------------------------------------
// Result-shape detectors — every rates primitive returns a dict whose shape
// follows a small number of canonical patterns.  We sniff the dict so we can
// render aesthetically without per-primitive view code.
// ---------------------------------------------------------------------------

/** Names of fields the rates substrate uses for canonical TimeSeries. */
const TIME_SERIES_FIELD_NAMES = [
  'time_series',
  'time_series_spread',
  'time_series_zscore',
  'time_series_change_zscore',
  'time_series_forward',
];

type DetectedSeries = {
  field: string;
  /** "value" | "spread_bps" | "z_score" | "yield_pct" | "forward_pct" | … */
  valueKey: string;
  rows: Array<{ date: string; value: number | null }>;
  unit: string;
};

function detectValueKey(rows: Array<Record<string, unknown>>): string {
  if (rows.length === 0) return 'value';
  const sample = rows[0];
  // Search keys in canonical priority order.
  const candidates = [
    'spread_bps',
    'change_z_score',
    'z_score',
    'yield_pct',
    'forward_pct',
    'value',
    'level',
  ];
  for (const k of candidates) {
    if (k in sample && typeof sample[k] === 'number') return k;
  }
  // Fall back to the first numeric key that's not "date".
  for (const [k, v] of Object.entries(sample)) {
    if (k === 'date') continue;
    if (typeof v === 'number') return k;
  }
  return 'value';
}

function unitForField(field: string, valueKey: string): string {
  if (field.includes('zscore') || valueKey.includes('z_score')) return 'σ';
  if (field === 'time_series_forward' || valueKey === 'forward_pct') return '%';
  if (valueKey === 'spread_bps') return 'bps';
  if (valueKey === 'yield_pct' || valueKey === 'level') return '%';
  return '';
}

function extractTimeSeries(
  output: Record<string, unknown>,
): DetectedSeries[] {
  const out: DetectedSeries[] = [];
  for (const field of TIME_SERIES_FIELD_NAMES) {
    const raw = output[field];
    if (!Array.isArray(raw) || raw.length === 0) continue;
    const rows = raw as Array<Record<string, unknown>>;
    const valueKey = detectValueKey(rows);
    const cleaned = rows
      .map((r) => {
        const date = String(r.date ?? '');
        const v = r[valueKey];
        return {
          date,
          value:
            typeof v === 'number' && Number.isFinite(v) ? v : null,
        };
      })
      .filter((r) => r.date.length > 0);
    if (cleaned.length === 0) continue;
    out.push({
      field,
      valueKey,
      rows: cleaned,
      unit: unitForField(field, valueKey),
    });
  }
  return out;
}

function extractScalarMetrics(
  output: Record<string, unknown>,
): MetricItem[] {
  const items: MetricItem[] = [];
  // Look at every top-level key whose value is a number / string scalar
  // and present.  Skip arrays + nested objects.  Cap at 8 to keep the
  // strip readable.
  for (const [k, v] of Object.entries(output)) {
    if (Array.isArray(v) || (v && typeof v === 'object')) continue;
    if (v === null || v === undefined) continue;
    if (k === 'as_of_date') continue; // shown elsewhere
    const label = humanLabel(k);
    if (typeof v === 'number') {
      if (!Number.isFinite(v)) continue;
      const tone =
        k.includes('z_score')
          ? toneForZScore(v)
          : k.includes('change')
            ? toneForChange(v)
            : 'neutral';
      const unit = k.includes('bps')
        ? 'bps'
        : k.includes('pct') || k.includes('yield')
          ? '%'
          : k.includes('z_score')
            ? 'σ'
            : undefined;
      items.push({
        label,
        value:
          k.includes('z_score') || k.includes('pct') || k.includes('yield')
            ? v.toFixed(3)
            : k.includes('change')
              ? formatSigned(v, 2)
              : v.toFixed(2),
        unit,
        tone,
      });
    } else if (typeof v === 'string') {
      items.push({ label, value: v });
    }
    if (items.length >= 8) break;
  }
  return items;
}

function humanLabel(snake: string): string {
  return snake
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/Bps/g, 'bps')
    .replace(/Pct/g, '%')
    .replace(/Z Score/g, 'Z-score');
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PrimitiveModelView({
  toolName,
  initialParams,
}: PrimitiveModelViewProps) {
  const { data: card, isLoading: cardLoading, error: cardError } = useTool(toolName);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      {cardError ? (
        <ErrorCard message={cardError.message} />
      ) : cardLoading || !card ? (
        <LoadingShell label={toolName} />
      ) : (
        <PrimitiveModelBody card={card} initialParams={initialParams} />
      )}
    </div>
  );
}

function LoadingShell({ label }: { label: string }) {
  return (
    <div className="px-6 py-10">
      <div className="card flex h-[320px] items-center justify-center text-[12px] text-fg-muted">
        Loading {label}…
      </div>
    </div>
  );
}

function ErrorCard({ message }: { message: string }) {
  return (
    <div className="px-6 py-10">
      <div className="card flex items-start gap-3 px-5 py-4">
        <AlertCircle size={16} className="mt-0.5 shrink-0 text-coral-300" />
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-fg-primary">
            Failed to load primitive
          </div>
          <div className="mt-1 text-[11.5px] text-fg-secondary">
            {message}
          </div>
        </div>
      </div>
    </div>
  );
}

function PrimitiveModelBody({
  card,
  initialParams,
}: {
  card: ToolCard;
  initialParams: Record<string, string>;
}) {
  // ----- form state ------------------------------------------------------
  const orderedFields = useMemo(() => sortFields(card.input_fields), [card]);

  const [values, setValues] = useState<Record<string, string>>(() => {
    const seed: Record<string, string> = {};
    for (const f of orderedFields) {
      seed[f.name] = initialParams[f.name] ?? defaultStringFor(f);
    }
    return seed;
  });

  // Re-seed when the user navigates to another primitive in the same view.
  useEffect(() => {
    const seed: Record<string, string> = {};
    for (const f of orderedFields) {
      seed[f.name] = initialParams[f.name] ?? defaultStringFor(f);
    }
    setValues(seed);
    setRunResult(null);
    setRunError(null);
  // We deliberately key on toolName via the parent re-mount; this is a
  // best-effort reset when the param order shifts.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.tool_name]);

  // ----- run state -------------------------------------------------------
  const [isRunning, setIsRunning] = useState(false);
  const [runResult, setRunResult] = useState<PrimitiveRunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const handleRun = async () => {
    setIsRunning(true);
    setRunError(null);
    try {
      const params: Record<string, unknown> = {};
      for (const f of orderedFields) {
        const v = values[f.name];
        if (v === '' || v === undefined) {
          if (f.required && f.default === null) {
            // Let the backend surface the validation error so we don't
            // over-prescribe what counts as "missing".
            continue;
          }
          continue;
        }
        params[f.name] = coerceForType(v, f.type);
      }
      const res = await runPrimitive(card.tool_name, params);
      setRunResult(res);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsRunning(false);
    }
  };

  // ----- derived: parse output if we have one ----------------------------
  const detected: {
    series: DetectedSeries[];
    metrics: MetricItem[];
    raw: Record<string, unknown>;
  } | null = useMemo(() => {
    if (!runResult || !runResult.ok) return null;
    return {
      series: extractTimeSeries(runResult.output),
      metrics: extractScalarMetrics(runResult.output),
      raw: runResult.output,
    };
  }, [runResult]);

  return (
    <>
      {/* Header ---------------------------------------------------------- */}
      <header className="flex flex-col gap-2 border-b border-line-subtle px-6 py-4">
        <div className="flex items-baseline justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="kicker text-ice-300/80">{card.domain}</span>
              {card.category ? (
                <span className="rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] text-[9.5px] uppercase tracking-[0.07em] text-fg-muted">
                  {card.category}
                </span>
              ) : null}
              <span className="rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] text-[9.5px] uppercase tracking-[0.07em] text-fg-muted mono">
                {card.tool_name}
              </span>
            </div>
            <h1 className="mt-1 text-[17px] font-semibold tracking-[-0.01em] text-fg-primary">
              {humanLabel(card.tool_name.replace(/_tool$/, ''))}
            </h1>
            <p className="mt-1 max-w-3xl text-[12px] leading-[1.55] text-fg-secondary">
              {card.description || card.methodology.what_it_does}
            </p>
          </div>
        </div>
      </header>

      {/* Body ------------------------------------------------------------ */}
      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[320px_1fr]">
        {/* Controls rail */}
        <aside className="border-r border-line-subtle bg-white/[0.008] px-5 py-5 lg:overflow-y-auto">
          <ControlsRail
            fields={orderedFields}
            values={values}
            onChange={(k, v) =>
              setValues((prev) => ({ ...prev, [k]: v }))
            }
            onRun={handleRun}
            isRunning={isRunning}
          />
          {card.conventions.length > 0 && (
            <ConventionsBlock card={card} />
          )}
        </aside>

        {/* Result canvas */}
        <section className="flex min-h-0 flex-col overflow-y-auto px-6 py-5">
          {runError && (
            <div className="card mb-5 flex items-start gap-3 border-coral-400/30 px-4 py-3">
              <AlertCircle size={14} className="mt-0.5 shrink-0 text-coral-300" />
              <div className="min-w-0">
                <div className="text-[12px] font-semibold text-coral-200">
                  Network error
                </div>
                <div className="mt-1 text-[11.5px] text-fg-secondary">
                  {runError}
                </div>
              </div>
            </div>
          )}

          {runResult && !runResult.ok && (
            <div className="card mb-5 flex items-start gap-3 border-coral-400/30 px-4 py-3">
              <AlertCircle size={14} className="mt-0.5 shrink-0 text-coral-300" />
              <div className="min-w-0">
                <div className="text-[12px] font-semibold text-coral-200">
                  Primitive returned an error
                </div>
                <div className="mt-1 text-[11.5px] text-fg-secondary">
                  {runResult.error}
                </div>
              </div>
            </div>
          )}

          {!runResult && !runError && (
            <EmptyResultPlaceholder card={card} />
          )}

          {detected && (
            <ResultCanvas
              card={card}
              series={detected.series}
              metrics={detected.metrics}
              raw={detected.raw}
            />
          )}
        </section>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Controls rail
// ---------------------------------------------------------------------------

function ControlsRail({
  fields,
  values,
  onChange,
  onRun,
  isRunning,
}: {
  fields: ToolFieldDescriptor[];
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onRun: () => void;
  isRunning: boolean;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <SlidersHorizontal size={13} className="text-ice-300" />
        <h2 className="text-[12px] font-semibold uppercase tracking-[0.1em] text-fg-secondary">
          Controls
        </h2>
      </div>

      <div className="space-y-3">
        {fields.map((field) => (
          <ControlInput
            key={field.name}
            field={field}
            value={values[field.name] ?? ''}
            onChange={(v) => onChange(field.name, v)}
          />
        ))}
      </div>

      <button
        type="button"
        onClick={onRun}
        disabled={isRunning}
        className={cn(
          'mt-2 flex w-full items-center justify-center gap-2 rounded-lg border px-3 py-2 text-[12.5px] font-semibold tracking-[-0.005em] transition-all duration-150 ease-sleek',
          isRunning
            ? 'cursor-wait border-line-soft bg-white/[0.02] text-fg-muted'
            : 'border-ice-400/40 bg-gradient-to-b from-ice-500/25 to-ice-700/25 text-ice-100 hover:border-ice-400/60 hover:from-ice-500/35 hover:to-ice-700/35',
        )}
      >
        {isRunning ? (
          <>
            <Loader2 size={13} className="animate-spin" />
            Running…
          </>
        ) : (
          <>
            <Play size={12} />
            Run primitive
          </>
        )}
      </button>
    </div>
  );
}

function ControlInput({
  field,
  value,
  onChange,
}: {
  field: ToolFieldDescriptor;
  value: string;
  onChange: (v: string) => void;
}) {
  const isNumber =
    field.type.toLowerCase().includes('integer') ||
    field.type.toLowerCase().includes('number');
  const isBool =
    field.type.toLowerCase() === 'boolean' ||
    field.type.toLowerCase() === 'bool';
  const examples = (field.examples ?? []).map(String);

  return (
    <label className="block">
      <div className="flex items-baseline justify-between">
        <span className="mono text-[10.5px] text-fg-secondary">
          {field.name}
          {field.required ? (
            <span className="ml-1 text-coral-300/80">*</span>
          ) : null}
        </span>
        <span className="text-[9px] uppercase tracking-[0.08em] text-fg-faint">
          {field.type}
        </span>
      </div>

      {isBool ? (
        <select
          value={value || 'false'}
          onChange={(e) => onChange(e.target.value)}
          className="mono mt-1 w-full appearance-none rounded-md border border-line-soft bg-white/[0.02] px-2.5 py-1.5 text-[11.5px] text-fg-primary transition-colors hover:border-ice-400/40 focus:border-ice-400/60 focus:outline-none"
        >
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : examples.length > 0 ? (
        <div className="relative mt-1">
          <input
            list={`opts-${field.name}`}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={examples[0]}
            type={isNumber ? 'number' : 'text'}
            className="mono w-full rounded-md border border-line-soft bg-white/[0.02] px-2.5 py-1.5 text-[11.5px] text-fg-primary transition-colors placeholder:text-fg-faint hover:border-ice-400/40 focus:border-ice-400/60 focus:outline-none"
          />
          <datalist id={`opts-${field.name}`}>
            {examples.map((ex) => (
              <option key={ex} value={ex} />
            ))}
          </datalist>
        </div>
      ) : (
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          type={isNumber ? 'number' : 'text'}
          placeholder={
            field.default !== null && field.default !== undefined
              ? String(field.default)
              : ''
          }
          className="mono mt-1 w-full rounded-md border border-line-soft bg-white/[0.02] px-2.5 py-1.5 text-[11.5px] text-fg-primary transition-colors placeholder:text-fg-faint hover:border-ice-400/40 focus:border-ice-400/60 focus:outline-none"
        />
      )}

      {field.description ? (
        <p className="mt-1 text-[10.5px] leading-snug text-fg-faint">
          {field.description}
        </p>
      ) : null}
    </label>
  );
}

function ConventionsBlock({ card }: { card: ToolCard }) {
  return (
    <div className="mt-7">
      <div className="mb-2 flex items-center gap-2">
        <Settings2 size={13} className="text-ice-300" />
        <h2 className="text-[12px] font-semibold uppercase tracking-[0.1em] text-fg-secondary">
          Conventions ({card.conventions.length})
        </h2>
      </div>
      <p className="mb-3 text-[10.5px] leading-snug text-fg-faint">
        Defaults shipped with this primitive — sourced from each
        bundled <code className="mono text-ice-300">config.yaml</code>.
      </p>
      <div className="space-y-2">
        {card.conventions.map((c) => (
          <div
            key={c.name}
            className="rounded-md border border-line-subtle bg-white/[0.012] px-2.5 py-2"
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="mono text-[10.5px] text-ice-200">
                {c.name}
              </span>
              <span className="mono shrink-0 text-[10.5px] text-fg-primary">
                {String(c.value)}
              </span>
            </div>
            <p className="mt-1 text-[10px] leading-snug text-fg-faint">
              {c.rationale}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty + result canvas
// ---------------------------------------------------------------------------

function EmptyResultPlaceholder({ card }: { card: ToolCard }) {
  return (
    <div className="flex h-full min-h-[400px] flex-col items-center justify-center text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-line-soft bg-white/[0.02]">
        <Sigma size={16} className="text-ice-300" />
      </div>
      <h3 className="mt-3 text-[13px] font-semibold text-fg-primary">
        Configure inputs and run
      </h3>
      <p className="mt-1 max-w-md text-[11.5px] text-fg-secondary">
        {card.methodology.what_it_does}
      </p>
      {card.methodology.assumptions.length > 0 && (
        <div className="mt-5 max-w-lg rounded-lg border border-line-subtle bg-white/[0.008] px-4 py-3 text-left">
          <p className="kicker mb-1.5 text-fg-muted">Assumptions</p>
          <ul className="space-y-1">
            {card.methodology.assumptions.slice(0, 4).map((a, i) => (
              <li key={i} className="flex gap-2 text-[11px] text-fg-secondary">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-fg-faint" />
                <span>{a}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ResultCanvas({
  card,
  series,
  metrics,
  raw,
}: {
  card: ToolCard;
  series: DetectedSeries[];
  metrics: MetricItem[];
  raw: Record<string, unknown>;
}) {
  // Pick the first non-z-score series as the headline chart, the first
  // z-score series as the bottom ribbon (mirrors the SpreadView layout).
  const headline = series.find((s) => !s.field.includes('zscore')) ?? series[0];
  const zSeries = series.find((s) => s.field.includes('zscore'));

  return (
    <div className="flex flex-col gap-5">
      {metrics.length > 0 && (
        <section className="card px-5 py-4">
          <WorkspaceMetrics
            items={metrics}
            title="Snapshot"
            desktopCols={Math.min(6, Math.max(4, metrics.length)) as 4 | 5 | 6}
          />
        </section>
      )}

      {headline && (
        <section className="card overflow-hidden">
          <div className="flex items-baseline justify-between px-5 pt-4 pb-1">
            <div>
              <div className="kicker">{card.domain} · {headline.field}</div>
              <p className="mt-0.5 text-[11px] text-fg-muted">
                {headline.rows.length} obs ·{' '}
                {headline.rows[0]?.date} → {headline.rows[headline.rows.length - 1]?.date}
              </p>
            </div>
            {headline.unit && (
              <span className="mono rounded-md border border-line-soft bg-white/[0.02] px-1.5 py-[1px] text-[10px] text-fg-secondary">
                {headline.unit}
              </span>
            )}
          </div>
          <div className="px-2 pt-2">
            <WorkspaceChart
              data={headline.rows.map<WorkspaceChartPoint>((r) => ({
                date: r.date,
                value: r.value ?? Number.NaN,
              }))}
              unit={headline.unit}
              valueDecimals={
                headline.unit === 'bps' ? 1 : headline.unit === '%' ? 3 : 2
              }
              tone={headline.unit === 'bps' ? 'rates' : 'rates'}
              height={300}
              seriesName={headline.field}
            />
          </div>
          {zSeries && (
            <div className="border-t border-line-subtle px-2 pt-1 pb-2">
              <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-[0.06em] text-fg-faint">
                {zSeries.field}
              </div>
              <WorkspaceZScoreChart
                data={zSeries.rows.map((r) => ({
                  date: r.date,
                  z_score: r.value,
                }))}
                height={140}
              />
            </div>
          )}
        </section>
      )}

      {series.length === 0 && (
        <section className="card px-5 py-4">
          <p className="kicker mb-2">Output</p>
          <p className="text-[11.5px] text-fg-secondary">
            This primitive returned no time-series fields.  Showing the raw
            output payload below for inspection.
          </p>
          <pre className="mono mt-3 max-h-[480px] overflow-auto rounded-md border border-line-soft bg-white/[0.012] p-3 text-[10.5px] leading-[1.55] text-fg-secondary">
            {JSON.stringify(raw, null, 2)}
          </pre>
        </section>
      )}

      <section className="card px-5 py-4">
        <p className="kicker mb-2">Lineage</p>
        <p className="text-[11px] leading-relaxed text-fg-secondary">
          Computed in-process by{' '}
          <code className="mono text-ice-300">{card.tool_name}</code>
          {' · '}
          <span className="text-fg-muted">
            {card.methodology.what_it_does.split('.')[0]}
          </span>
        </p>
      </section>
    </div>
  );
}
