// ============================================================================
// AutoRenderer — fallback for primitives without a bespoke renderer
// ----------------------------------------------------------------------------
// Sniffs the output dict for canonical shapes and renders accordingly:
//   - top-level scalar metrics → KPI strip
//   - any field whose name starts with `time_series` AND whose value is a
//     non-empty array of {date, value, ...} rows → multi-series line panel
//   - else → JSON pretty-print as the last-resort inspector
//
// This keeps newly-registered primitives renderable from day one without
// any per-primitive frontend work — the workspace is metadata-driven all
// the way down.
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

const SERIES_TONES: ChartTone[] = ['blue', 'rates', 'amber', 'green', 'coral', 'neutral'];

type Output = Record<string, unknown>;

export function AutoRenderer({ output }: { output: Output }) {
  const metrics = pickScalarMetrics(output);
  const tsFields = pickTimeSeriesFields(output);

  return (
    <div className="space-y-5">
      {metrics.length > 0 ? <KpiStrip metrics={metrics} /> : null}

      {tsFields.length > 0 ? (
        <TimeSeriesGrid fields={tsFields} />
      ) : null}

      {metrics.length === 0 && tsFields.length === 0 ? (
        <RawJsonCard output={output} />
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// KPI strip — top-level scalar metrics
// ---------------------------------------------------------------------------

type KpiItem = {
  label: string;
  value: string;
  unit?: string;
  tone?: 'mint' | 'coral' | 'amber';
};

function pickScalarMetrics(output: Output): KpiItem[] {
  const metrics = output['current_metrics'] as Record<string, unknown> | undefined;
  const source = metrics ?? output;
  const items: KpiItem[] = [];
  for (const [k, v] of Object.entries(source)) {
    if (Array.isArray(v) || (v && typeof v === 'object')) continue;
    if (v === null || v === undefined) continue;
    if (k === 'as_of_date') continue;
    if (typeof v === 'number') {
      if (!Number.isFinite(v)) continue;
      items.push({
        label: humanLabel(k),
        value: formatNumber(v, k),
        unit: unitForKey(k),
        tone: toneForKey(k, v),
      });
    } else if (typeof v === 'string') {
      items.push({ label: humanLabel(k), value: v });
    }
    if (items.length >= 8) break;
  }
  return items;
}

function KpiStrip({ metrics }: { metrics: KpiItem[] }) {
  const cols = Math.min(6, Math.max(2, metrics.length));
  return (
    <section className="card px-5 py-4">
      <div className="kicker mb-3">Snapshot</div>
      <div className={cn('grid gap-3', `grid-cols-2 md:grid-cols-${cols}`)}>
        {metrics.map((m) => (
          <div key={m.label}>
            <p className="text-[9.5px] uppercase tracking-[0.14em] text-fg-faint">
              {m.label}
            </p>
            <p
              className={cn(
                'mono mt-1 text-[14.5px] text-fg-primary',
                m.tone === 'mint' && 'text-mint-300',
                m.tone === 'coral' && 'text-coral-300',
                m.tone === 'amber' && 'text-amber-300',
              )}
            >
              {m.value}
              {m.unit ? <span className="ml-1 text-[10px] text-fg-faint">{m.unit}</span> : null}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// TimeSeries grid — one card per time_series field
// ---------------------------------------------------------------------------

type TsField = {
  fieldName: string;
  rows: Array<{ date: string; value: number | null }>;
  unit?: string;
};

function pickTimeSeriesFields(output: Output): TsField[] {
  const out: TsField[] = [];
  for (const [k, v] of Object.entries(output)) {
    if (!Array.isArray(v) || v.length === 0) continue;
    // Single-Series field: {date, value, ...} list.
    const sample = v[0] as Record<string, unknown> | undefined;
    if (sample && typeof sample === 'object' && 'date' in sample) {
      out.push({
        fieldName: k,
        rows: v.map((r: Record<string, unknown>) => ({
          date: String(r['date'] ?? ''),
          value: pickNumeric(r),
        })).filter((r) => r.date.length > 0),
        unit: undefined,
      });
      continue;
    }
    // List-of-TimeSeries field: [{series_name, units, rows: [{date, value}]}].
    const subSample = v[0] as Record<string, unknown> | undefined;
    if (
      subSample &&
      typeof subSample === 'object' &&
      Array.isArray(subSample['rows']) &&
      'series_name' in subSample
    ) {
      // Flatten each member as its own field.
      for (const ts of v as Array<Record<string, unknown>>) {
        const rows = (ts['rows'] ?? []) as Array<Record<string, unknown>>;
        out.push({
          fieldName: String(ts['series_name'] ?? `${k}_series`),
          rows: rows.map((r) => ({
            date: String(r['date'] ?? ''),
            value: typeof r['value'] === 'number' && Number.isFinite(r['value']) ? r['value'] : null,
          })).filter((r) => r.date.length > 0),
          unit: ts['units'] as string | undefined,
        });
      }
    }
  }
  return out;
}

function TimeSeriesGrid({ fields }: { fields: TsField[] }) {
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      {fields.map((f, i) => (
        <SingleTsCard key={`${f.fieldName}-${i}`} field={f} tone={SERIES_TONES[i % SERIES_TONES.length]} />
      ))}
    </div>
  );
}

function SingleTsCard({ field, tone }: { field: TsField; tone: ChartTone }) {
  return (
    <section className="card overflow-hidden">
      <div className="flex items-baseline justify-between border-b border-line-subtle px-5 py-3">
        <div className="kicker">{field.fieldName}</div>
        <span className="mono text-[10px] text-fg-faint">
          {field.unit ?? ''}
        </span>
      </div>
      <div className="px-3 py-3">
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={field.rows} margin={{ top: 5, right: 18, left: 0, bottom: 5 }}>
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

function RawJsonCard({ output }: { output: Output }) {
  return (
    <section className="card px-5 py-4">
      <div className="kicker mb-2">Raw output (no auto-render available)</div>
      <pre className="mono max-h-[480px] overflow-auto rounded-md border border-line-soft bg-white/[0.012] p-3 text-[10.5px] leading-[1.55] text-fg-secondary">
        {JSON.stringify(output, null, 2)}
      </pre>
    </section>
  );
}

// ---------------------------------------------------------------------------

function pickNumeric(row: Record<string, unknown>): number | null {
  const candidates = ['value', 'spread_bps', 'z_score', 'yield_pct', 'forward_pct', 'level'];
  for (const k of candidates) {
    const v = row[k];
    if (typeof v === 'number' && Number.isFinite(v)) return v;
  }
  for (const [k, v] of Object.entries(row)) {
    if (k === 'date') continue;
    if (typeof v === 'number' && Number.isFinite(v)) return v;
  }
  return null;
}

function humanLabel(snake: string): string {
  return snake.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function unitForKey(k: string): string | undefined {
  if (k.includes('bps')) return 'bps';
  if (k.includes('pct') || k.endsWith('_yield') || k.endsWith('_rate')) return '%';
  if (k.includes('z_score')) return 'σ';
  return undefined;
}

function toneForKey(k: string, v: number): KpiItem['tone'] {
  if (k.includes('z_score') || k.includes('zscore')) {
    if (Math.abs(v) > 2) return 'coral';
    if (Math.abs(v) > 1.5) return 'amber';
  }
  return undefined;
}

function formatNumber(v: number, k: string): string {
  if (k.includes('z_score') || k.includes('zscore')) return v.toFixed(2);
  if (k.includes('pct') || k.includes('share')) return v.toFixed(3);
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  if (Math.abs(v) >= 1) return v.toFixed(2);
  return v.toFixed(4);
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });
}
