// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for ``scan_ois_extremes_tool``.
// SCANNER shape (OIS par-swap-rate universe).
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.1 + §5 every primitive
// claiming ``custom_build_surface`` ships an extended view; this is the
// universe-scan canvas mounted by ``VirtualPrimitiveCanvas`` AND by the
// multi-tool DAG modal infrastructure when the compact card calls
// ``onExpand``.  The scanner's headline data is a RANKED LIST, not a time
// series — this component owns its own scanner-shaped layout (identity row →
// scan-summary callouts → controls strip → distribution histogram → ranked
// detail table → methodology card → lineage footer) and reuses the shared
// ``ControlsStrip`` / ``MethodologyCard`` / ``LineageFooter`` /
// ``FreshnessPill`` elements so the chrome matches the rest of the Build
// catalogue.
//
// Mirror of the sovereign twin
// ``../../scan_extremes_tool/surfaces/BuildExtended.tsx`` (P3 — the two
// LEVEL-metric scanner modules cannot drift).  Differences are the OIS
// vocabulary (markets / overnight indices instead of countries / curves),
// the RATE-space level column, the per-row deep-link target
// (``get_ois_rate_level_tool``), and the extra ``field_name`` control (the
// OIS schema exposes PX_LAST / PX_BID / PX_ASK).
// ============================================================================

import { useNavigate } from 'react-router-dom';
import { Activity, Info, Search } from 'lucide-react';
import { useRequestFocusedMode } from '@/components/build/lib/focusedMode';
import {
  ControlsStrip,
  FreshnessPill,
  LineageFooter,
  MethodologyCard,
  toneTextClass,
  type ControlDescriptor,
} from '@/components/shared/build';
import {
  OIS_SCANNER_CURVE_OPTIONS,
  SCAN_OIS_EXTREMES_COMPACT_CAVEAT,
  buildHistogramBins,
  buildMethodologyRows,
  buildReferenceChips,
  changeBpsTone,
  formatChangeBps,
  formatPercentile,
  formatRatePct,
  formatZScore,
  instrumentFlag,
  instrumentIndexShort,
  instrumentMarketLabel,
  oisFamilyFor,
  parseScanSummary,
  useOisScanner,
  zScoreTone,
} from './oisScannerShared';
import type { BuildExtendedProps } from '@/components/shared/build';
import type { ScanOisExtremesResultRow } from '@/types/rates';

// ---------------------------------------------------------------------------
// Defaults — the controls strip and the fetch hook share one source of
// truth.  The backend resolves None ⇒ schema default (top_n=10,
// min_abs_z_score=1.5, field_name='PX_LAST'); the extended view surfaces
// the schema default as the displayed value and passes the value through
// to the hook so user overrides round-trip via URL state.
// ---------------------------------------------------------------------------

const DEFAULT_TOP_N = '10';
const DEFAULT_MIN_ABS_Z = '1.5';
const DEFAULT_FIELD_NAME = 'PX_LAST';

const TOP_N_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '5', label: 'Top 5' },
  { value: '10', label: 'Top 10' },
  { value: '20', label: 'Top 20' },
  { value: '50', label: 'Top 50' },
];
const MIN_ABS_Z_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '1.0', label: '|z| ≥ 1.0' },
  { value: '1.5', label: '|z| ≥ 1.5' },
  { value: '2.0', label: '|z| ≥ 2.0' },
  { value: '2.5', label: '|z| ≥ 2.5' },
];
const FIELD_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'PX_LAST', label: 'PX_LAST (mid)' },
  { value: 'PX_BID', label: 'PX_BID' },
  { value: 'PX_ASK', label: 'PX_ASK' },
];

const ALL_CURVE_FAMILIES_VALUE = '__ALL__';

const CURVE_FAMILY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: ALL_CURVE_FAMILIES_VALUE, label: 'All OIS markets (G6)' },
  ...OIS_SCANNER_CURVE_OPTIONS,
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const BuildExtended: React.FC<BuildExtendedProps> = ({
  toolName,
  params,
  onParamsChange,
}) => {
  const navigate = useNavigate();
  useRequestFocusedMode(true);

  const topNStr = params.top_n || DEFAULT_TOP_N;
  const minAbsZStr = params.min_abs_z_score || DEFAULT_MIN_ABS_Z;
  const curveFamiliesParam = params.curve_families || ALL_CURVE_FAMILIES_VALUE;
  const fieldName = params.field_name || DEFAULT_FIELD_NAME;

  const curveFamilies =
    curveFamiliesParam === ALL_CURVE_FAMILIES_VALUE
      ? undefined
      : curveFamiliesParam.split(',').map((s) => s.trim()).filter(Boolean);

  const { data, isLoading, errorMessage } = useOisScanner({
    curveFamilies,
    topN: Number(topNStr),
    minAbsZScore: Number(minAbsZStr),
    fieldName,
  });

  const summary = parseScanSummary(data?.scan_summary ?? '');
  const rows = data?.results ?? [];
  const bins = buildHistogramBins(rows);
  const highCount = rows.filter((r) => (r.z_score ?? 0) > 0).length;
  const lowCount = rows.filter((r) => (r.z_score ?? 0) < 0).length;
  const extremeCount = rows.filter(
    (r) => Math.abs(r.z_score ?? 0) >= 2.0,
  ).length;
  const maxBinCount = bins.reduce((m, b) => Math.max(m, b.count), 0) || 1;
  const asOfDate = rows[0]?.as_of_date ?? null;

  // ----- Param update on control change -----
  const pushParams = (nextParams: Record<string, string>) => {
    if (onParamsChange) {
      onParamsChange(nextParams);
      return;
    }
    const nextCtx = encodeURIComponent(
      JSON.stringify({
        tools: [{ tool: toolName, params: nextParams }],
        tool_count: 1,
      }),
    );
    navigate(`/workspace?context=${nextCtx}`, { replace: true });
  };

  const handleControlChange = (name: string, value: string) => {
    const next = { ...params, [name]: value };
    pushParams(next);
  };

  const handleReset = () => {
    pushParams({
      top_n: DEFAULT_TOP_N,
      min_abs_z_score: DEFAULT_MIN_ABS_Z,
      curve_families: ALL_CURVE_FAMILIES_VALUE,
      field_name: DEFAULT_FIELD_NAME,
    });
  };

  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_families',
      label: 'Curve scope',
      kind: 'enum',
      value: curveFamiliesParam,
      options: CURVE_FAMILY_OPTIONS,
    },
    {
      name: 'min_abs_z_score',
      label: 'Threshold',
      kind: 'enum',
      value: minAbsZStr,
      options: MIN_ABS_Z_OPTIONS,
    },
    {
      name: 'top_n',
      label: 'Top N',
      kind: 'enum',
      value: topNStr,
      options: TOP_N_OPTIONS,
    },
    {
      name: 'field_name',
      label: 'Field',
      kind: 'enum',
      value: fieldName,
      options: FIELD_OPTIONS,
    },
  ];

  return (
    <div
      className="flex h-full min-h-0 flex-col overflow-y-auto"
      data-testid="ois-scanner-extended"
    >
      {/* ---------- Title section ---------- */}
      <section className="grid grid-cols-1 gap-4 border-b border-line-subtle px-6 pt-5 pb-5 lg:grid-cols-[1fr_auto]">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-fg-muted">
            <Search size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
            <span className="font-semibold">OIS UNIVERSE SCAN</span>
            <span className="text-fg-faint" aria-hidden>•</span>
            <span>SCANNER</span>
            <span className="text-fg-faint" aria-hidden>•</span>
            <span>DETERMINISTIC</span>
          </div>
          <div className="flex items-baseline gap-3">
            <h1 className="text-[24px] font-semibold leading-tight text-fg-primary">
              OIS Rate Extremes
            </h1>
            <span className="text-[13px] text-fg-secondary">
              (252d Z-Score)
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-fg-secondary">
            {summary.scanned != null && (
              <span>{summary.scanned} OIS instruments scanned</span>
            )}
            {summary.flagged != null && (
              <>
                <span className="text-fg-faint" aria-hidden>·</span>
                <span className="text-amber-300">{summary.flagged} Flagged</span>
              </>
            )}
            {asOfDate && (
              <>
                <span className="text-fg-faint" aria-hidden>·</span>
                <span>As of {asOfDate}</span>
              </>
            )}
            <FreshnessPill freshness="fresh" />
          </div>
        </div>
        <div className="grid grid-flow-col auto-cols-[minmax(190px,1fr)] gap-3">
          <ScanSummaryCard
            scanned={summary.scanned}
            flagged={summary.flagged}
            threshold={summary.threshold}
          />
          <DistributionCard
            highCount={highCount}
            lowCount={lowCount}
            extremeCount={extremeCount}
          />
          <CoverageCard rows={rows} />
        </div>
      </section>

      {/* ---------- Controls strip ---------- */}
      <div className="px-6 pt-4">
        <ControlsStrip
          controls={controls}
          onChange={handleControlChange}
          onReset={handleReset}
        />
      </div>

      {/* ---------- Distribution histogram ---------- */}
      <div className="px-6 pt-4">
        <div className="card flex flex-col gap-3 px-4 py-4">
          <div className="flex items-baseline justify-between gap-3">
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-fg-muted">
              <Activity size={12} strokeWidth={1.75} aria-hidden />
              <span>Z-Score Distribution (flagged extremes)</span>
            </div>
            <span className="text-[11px] text-fg-faint">
              Histogram covers the returned top-{rows.length} rows; widen the
              threshold to fill the bins.
            </span>
          </div>
          <Histogram bins={bins} maxCount={maxBinCount} />
        </div>
      </div>

      {/* ---------- Scan summary callouts ---------- */}
      <div className="grid grid-cols-1 gap-3 px-6 pt-4 lg:grid-cols-3">
        <SummaryCallout
          tone="extreme-up"
          label="Biggest moves (high)"
          count={highCount}
          hint="Stems where the 252d-rolling par swap rate sits above its trailing-year mean by ≥ threshold."
        />
        <SummaryCallout
          tone="extreme-down"
          label="Biggest moves (low)"
          count={lowCount}
          hint="Stems where the 252d-rolling par swap rate sits below its trailing-year mean by ≥ threshold."
        />
        <SummaryCallout
          tone="elevated"
          label="Significant (|z| ≥ 2σ)"
          count={extremeCount}
          hint="Subset of the flagged stems at or beyond the 2σ envelope."
        />
      </div>

      {/* ---------- Ranked detail table ---------- */}
      <div className="px-6 pt-4">
        <RankedTable
          rows={rows}
          isLoading={isLoading}
          errorMessage={errorMessage ?? undefined}
          onOpenInBuild={(row) => {
            const ctx = encodeURIComponent(
              JSON.stringify({
                tools: [
                  {
                    tool: 'get_ois_rate_level_tool',
                    params: {
                      curve_family: row.curve_family,
                      tenor: row.tenor,
                    },
                  },
                ],
                tool_count: 1,
              }),
            );
            navigate(`/workspace?context=${ctx}`);
          }}
        />
      </div>

      {/* ---------- Methodology + lineage ---------- */}
      <div className="grid grid-cols-1 gap-3 px-6 pt-4 pb-6 lg:grid-cols-[2fr_3fr]">
        <ExtremityKeyCard />
        <MethodologyCard
          rows={data ? buildMethodologyRows(data, summary) : []}
          references={buildReferenceChips()}
        />
      </div>

      <LineageFooter
        lineage={{
          toolName,
          version: 'v1',
          kind: 'Universe scanner (deterministic)',
          providers: ['TimescaleDB', 'macro_data.v_market_data_daily_enriched'],
          asOf: asOfDate ?? undefined,
          freshness: 'fresh',
        }}
      />
    </div>
  );
};

export default BuildExtended;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ScanSummaryCard({
  scanned,
  flagged,
  threshold,
}: {
  scanned: number | null;
  flagged: number | null;
  threshold: number | null;
}) {
  return (
    <div className="card flex h-full flex-col gap-2 px-4 py-3">
      <span className="kicker text-fg-muted">SCAN SUMMARY</span>
      <div className="flex items-baseline gap-1.5">
        <span className="text-[26px] font-medium leading-none text-fg-primary">
          {flagged ?? '—'}
        </span>
        <span className="text-[14px] text-fg-secondary">/ {scanned ?? '—'}</span>
      </div>
      <span className="text-[11.5px] text-fg-secondary">
        Threshold {threshold != null ? `|z| ≥ ${threshold.toFixed(1)}σ` : '—'}
      </span>
    </div>
  );
}

function DistributionCard({
  highCount,
  lowCount,
  extremeCount,
}: {
  highCount: number;
  lowCount: number;
  extremeCount: number;
}) {
  return (
    <div className="card flex h-full flex-col gap-2 px-4 py-3">
      <span className="kicker text-fg-muted">DISTRIBUTION</span>
      <div className="flex items-baseline gap-2">
        <span className={`text-[20px] font-medium leading-none ${toneTextClass('extreme-up')}`}>
          {highCount}↑
        </span>
        <span className={`text-[20px] font-medium leading-none ${toneTextClass('extreme-down')}`}>
          {lowCount}↓
        </span>
      </div>
      <span className="text-[11.5px] text-fg-secondary">
        {extremeCount} at |z| ≥ 2σ
      </span>
    </div>
  );
}

function CoverageCard({
  rows,
}: {
  rows: ReadonlyArray<ScanOisExtremesResultRow>;
}) {
  // Surface the market coverage of the flagged extremes — the desk sees
  // WHICH OIS markets showed up in the top-N so the "is this just one
  // policy path or several?" question is answered at a glance.
  const markets = new Set<string>();
  for (const r of rows) {
    const meta = oisFamilyFor(r.curve_family);
    if (meta) markets.add(meta.marketShort);
  }
  return (
    <div className="card flex h-full flex-col gap-2 px-4 py-3">
      <span className="kicker text-fg-muted">MARKET COVERAGE</span>
      <div className="text-[15px] leading-tight text-fg-primary">
        {[...markets].join(' / ') || '—'}
      </div>
      <span className="text-[11px] leading-snug text-fg-secondary">
        {SCAN_OIS_EXTREMES_COMPACT_CAVEAT}
      </span>
    </div>
  );
}

function Histogram({
  bins,
  maxCount,
}: {
  bins: ReadonlyArray<{
    from: number;
    to: number;
    count: number;
    extreme: boolean;
    elevated: boolean;
  }>;
  maxCount: number;
}) {
  return (
    <div className="flex h-[140px] items-end gap-1.5">
      {bins.map((b, i) => {
        const heightPct = maxCount > 0 ? (b.count / maxCount) * 100 : 0;
        const tone = b.extreme
          ? b.from >= 0
            ? 'extreme-up'
            : 'extreme-down'
          : b.elevated
            ? 'elevated'
            : 'neutral';
        const barClass =
          tone === 'extreme-up'
            ? 'bg-coral-400/80'
            : tone === 'extreme-down'
              ? 'bg-mint-400/80'
              : tone === 'elevated'
                ? 'bg-amber-400/70'
                : 'bg-line-strong/70';
        return (
          <div key={i} className="flex flex-1 flex-col items-center gap-1">
            <div className="flex w-full flex-1 items-end">
              <div
                className={`w-full rounded-t ${barClass}`}
                style={{ height: `${heightPct}%` }}
                aria-label={`bin ${b.from} to ${b.to}: ${b.count} stems`}
                title={`[${b.from.toFixed(1)}, ${b.to.toFixed(1)}): ${b.count}`}
              />
            </div>
            <span className="text-[9.5px] text-fg-muted">
              {b.from.toFixed(1)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function SummaryCallout({
  tone,
  label,
  count,
  hint,
}: {
  tone: 'extreme-up' | 'extreme-down' | 'elevated';
  label: string;
  count: number;
  hint: string;
}) {
  return (
    <div className="card flex flex-col gap-1.5 px-4 py-3">
      <div className="flex items-baseline gap-2">
        <span className={`text-[20px] font-medium leading-none ${toneTextClass(tone)}`}>
          {count}
        </span>
        <span className="text-[12px] text-fg-secondary">{label}</span>
      </div>
      <span className="text-[11px] leading-snug text-fg-faint">{hint}</span>
    </div>
  );
}

function RankedTable({
  rows,
  isLoading,
  errorMessage,
  onOpenInBuild,
}: {
  rows: ReadonlyArray<ScanOisExtremesResultRow>;
  isLoading?: boolean;
  errorMessage?: string;
  onOpenInBuild: (row: ScanOisExtremesResultRow) => void;
}) {
  return (
    <div className="card overflow-hidden">
      <div className="border-b border-line-subtle px-4 py-3">
        <span className="kicker text-fg-muted">Ranked detail</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-[12.5px]">
          <thead>
            <tr className="border-b border-line-subtle text-left text-[10.5px] uppercase tracking-wide text-fg-muted">
              <th className="px-4 py-2 font-normal">#</th>
              <th className="px-3 py-2 font-normal">Market</th>
              <th className="px-3 py-2 font-normal">Index</th>
              <th className="px-3 py-2 font-normal">Tenor</th>
              <th className="px-3 py-2 text-right font-normal">Rate</th>
              <th className="px-3 py-2 text-right font-normal">1D Δ (bp)</th>
              <th className="px-3 py-2 text-right font-normal">Z-Score (252d)</th>
              <th className="px-3 py-2 text-right font-normal">252d %ile</th>
              <th className="px-4 py-2 text-right font-normal">Open</th>
            </tr>
          </thead>
          <tbody>
            {errorMessage ? (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-coral-300">
                  {errorMessage}
                </td>
              </tr>
            ) : isLoading ? (
              [0, 1, 2, 3, 4].map((i) => (
                <tr key={`sk-${i}`} className="border-b border-line-subtle">
                  <td className="px-4 py-2">
                    <div className="h-3 w-4 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2">
                    <div className="h-3 w-20 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2">
                    <div className="h-3 w-14 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2">
                    <div className="h-3 w-10 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="ml-auto h-3 w-12 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="ml-auto h-3 w-10 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="ml-auto h-3 w-12 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="ml-auto h-3 w-10 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="ml-auto h-3 w-16 animate-pulse rounded bg-line-subtle" />
                  </td>
                </tr>
              ))
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-fg-secondary">
                  No stems passed the threshold for this scan.
                </td>
              </tr>
            ) : (
              rows.map((row) => {
                const tone = zScoreTone(row.z_score);
                return (
                  <tr
                    key={`${row.curve_family}-${row.tenor}-${row.rank}`}
                    className="border-b border-line-subtle hover:bg-surface-overlay/60"
                  >
                    <td className={`px-4 py-2 font-mono text-[11.5px] ${toneTextClass(tone)}`}>
                      {row.rank}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-1.5 text-fg-primary">
                        {instrumentFlag(row) && (
                          <span className="text-[13px] leading-none">{instrumentFlag(row)}</span>
                        )}
                        <span>{instrumentMarketLabel(row)}</span>
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <span className="font-mono text-[12px] text-fg-secondary">
                        {instrumentIndexShort(row)}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono text-fg-secondary">{row.tenor}</td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-fg-primary">
                      {formatRatePct(row.current_rate_pct)}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${toneTextClass(changeBpsTone(row.daily_change_bps))}`}>
                      {formatChangeBps(row.daily_change_bps)}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${toneTextClass(tone)}`}>
                      {formatZScore(row.z_score)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-fg-secondary">
                      {formatPercentile(row.percentile_252d)}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        type="button"
                        onClick={() => onOpenInBuild(row)}
                        className="rounded-md border border-line-subtle bg-surface-overlay px-2 py-1 text-[11px] text-fg-secondary hover:border-line-strong hover:text-fg-primary"
                      >
                        Open in Build
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ExtremityKeyCard() {
  return (
    <div className="card flex flex-col gap-3 px-4 py-4">
      <span className="kicker text-fg-muted">Extremity key</span>
      <ul className="flex flex-col gap-2 text-[12px] text-fg-secondary">
        <li className="flex items-center gap-2">
          <span className="h-2 w-3 rounded-sm bg-coral-400/80" aria-hidden />
          <span>|z| ≥ 2.0σ · extreme (coral up / mint down)</span>
        </li>
        <li className="flex items-center gap-2">
          <span className="h-2 w-3 rounded-sm bg-amber-400/70" aria-hidden />
          <span>1.5 ≤ |z| &lt; 2.0σ · elevated (amber)</span>
        </li>
        <li className="flex items-center gap-2">
          <span className="h-2 w-3 rounded-sm bg-line-strong/70" aria-hidden />
          <span>|z| &lt; 1.5σ · within norm (neutral)</span>
        </li>
      </ul>
      <div className="flex items-start gap-2 rounded-md border border-line-subtle bg-surface-overlay px-3 py-2 text-[11px] leading-snug text-fg-secondary">
        <Info size={12} strokeWidth={1.5} className="mt-0.5 shrink-0 text-fg-muted" aria-hidden />
        <span>
          Click <span className="font-semibold text-fg-primary">Open in Build</span> on any row to drill into the per-stem
          <code className="mx-1 rounded bg-bg-surface px-1 font-mono text-[10.5px]">
            get_ois_rate_level_tool
          </code>
          with the selected (curve, tenor) bound.
        </span>
      </div>
    </div>
  );
}
