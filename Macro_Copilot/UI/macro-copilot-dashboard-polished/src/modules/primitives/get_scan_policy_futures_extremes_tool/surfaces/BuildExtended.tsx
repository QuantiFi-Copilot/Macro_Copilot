// ============================================================================
// surfaces/BuildExtended.tsx — Extended Build view for
// ``get_scan_policy_futures_extremes_tool``.  SCANNER shape (multi-metric).
// ----------------------------------------------------------------------------
// Per docs_revamped/03_standards/rendering_density.md §2.1 + §5 every new
// primitive ships an extended view; this is the universe-scan canvas for
// STIR (policy-futures) extremes.  Mirrors the scan_bond_futures_extremes /
// scan_inflation_swaps_extremes / scan_inflation_linkers_extremes sibling
// shape (identity row → top-right summary cards → controls strip →
// distribution histogram → ranked detail table → methodology card → lineage
// footer) but the policy-futures wire is MULTI-METRIC (implied-rate LEVEL /
// 1-day implied-rate CHANGE (bps) / volume LEVEL / open-interest LEVEL —
// each independently ranked).  Reuses the shared ControlsStrip /
// MethodologyCard / LineageFooter / FreshnessPill elements so the chrome
// matches the rest of the Build catalogue.
//
// Design reference: ./mockups/Extended.png.
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
  METRIC_REGISTRY,
  POLICY_FUTURES_SCANNER_COMPACT_CAVEAT,
  POLICY_FUTURES_SCANNER_CURVE_OPTIONS,
  buildHistogramBins,
  buildMethodologyRows,
  buildReferenceChips,
  dailyChangeDisplay,
  formatZScore,
  instrumentFlag,
  nativeUnits,
  nativeValue,
  narrativeTag,
  parseScanSummary,
  policyFuturesFamilyFor,
  stripPack,
  usePolicyFuturesScanner,
  zScoreTone,
} from './policyFuturesScannerShared';
import type { BuildExtendedProps } from '@/components/shared/build';
import type { ScanPolicyFuturesExtremesResultRow } from '@/types/rates';

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const DEFAULT_TOP_N = '5';
const DEFAULT_MIN_ABS_Z = '1.5';
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

const ALL_CURVE_FAMILIES_VALUE = '__ALL__';

const CURVE_FAMILY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: ALL_CURVE_FAMILIES_VALUE, label: 'All STIR (SOFR / EUR_SHORT_RATE / SONIA)' },
  ...POLICY_FUTURES_SCANNER_CURVE_OPTIONS,
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
  const asOfDate = params.as_of_date || undefined;

  const curveFamilies =
    curveFamiliesParam === ALL_CURVE_FAMILIES_VALUE
      ? undefined
      : curveFamiliesParam.split(',').map((s) => s.trim()).filter(Boolean);

  const { data, isLoading, errorMessage } = usePolicyFuturesScanner({
    curveFamilies,
    topN: Number(topNStr),
    minAbsZScore: Number(minAbsZStr),
    asOfDate,
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
    });
  };

  const controls: ReadonlyArray<ControlDescriptor> = [
    {
      name: 'curve_families',
      label: 'STIR scope',
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
      label: 'Top N / metric',
      kind: 'enum',
      value: topNStr,
      options: TOP_N_OPTIONS,
    },
  ];

  const anchorLabel = summary.asOfSpanStart && summary.asOfSpanEnd
    ? summary.asOfSpanStart === summary.asOfSpanEnd
      ? summary.asOfSpanStart
      : `${summary.asOfSpanStart} → ${summary.asOfSpanEnd}`
    : null;

  return (
    <div
      className="flex h-full min-h-0 flex-col overflow-y-auto"
      data-testid="policy-futures-scanner-extended"
    >
      {/* ---------- Title section ---------- */}
      <section className="grid grid-cols-1 gap-4 border-b border-line-subtle px-6 pt-5 pb-5 lg:grid-cols-[1fr_auto]">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-fg-muted">
            <Search size={12} strokeWidth={1.75} className="text-ice-300" aria-hidden />
            <span className="font-semibold">STIR UNIVERSE SCAN</span>
            <span className="text-fg-faint" aria-hidden>•</span>
            <span>SCANNER</span>
            <span className="text-fg-faint" aria-hidden>•</span>
            <span>DETERMINISTIC</span>
          </div>
          <div className="flex items-baseline gap-3">
            <h1 className="text-[24px] font-semibold leading-tight text-fg-primary">
              Policy Futures Extremes
            </h1>
            <span className="text-[13px] text-fg-secondary">
              (252d Z-Score)
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[12px] text-fg-secondary">
            {summary.scanned != null && (
              <span>{summary.scanned} STIR stems scanned</span>
            )}
            {summary.flaggedTotal != null && (
              <>
                <span className="text-fg-faint" aria-hidden>·</span>
                <span className="text-amber-300">{summary.flaggedTotal} Flagged</span>
              </>
            )}
            {anchorLabel && (
              <>
                <span className="text-fg-faint" aria-hidden>·</span>
                <span>As of {anchorLabel}</span>
              </>
            )}
            <FreshnessPill freshness="fresh" />
          </div>
        </div>
        <div className="grid grid-flow-col auto-cols-[minmax(190px,1fr)] gap-3">
          <ScanSummaryCard
            scoreable={summary.scoreable}
            flagged={summary.flaggedTotal}
            threshold={summary.threshold}
          />
          <DistributionCard
            highCount={highCount}
            lowCount={lowCount}
            extremeCount={extremeCount}
          />
          <CentralBankCard rows={rows} />
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
              <span>Z-Score Distribution (flagged extremes · all metrics)</span>
            </div>
            <span className="text-[11px] text-fg-faint">
              Histogram covers the returned {rows.length} multi-metric rows;
              widen the threshold to fill the bins.
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
          hint="Rows where the underlying metric sits above its trailing-year mean by ≥ threshold."
        />
        <SummaryCallout
          tone="extreme-down"
          label="Biggest moves (low)"
          count={lowCount}
          hint="Rows where the underlying metric sits below its trailing-year mean by ≥ threshold."
        />
        <SummaryCallout
          tone="elevated"
          label="Significant (|z| ≥ 2σ)"
          count={extremeCount}
          hint="Subset of the flagged rows at or beyond the 2σ envelope."
        />
      </div>

      {/* ---------- Per-metric breakdown ---------- */}
      <div className="px-6 pt-4">
        <MetricBreakdownCard summary={summary} />
      </div>

      {/* ---------- Ranked detail table ---------- */}
      <div className="px-6 pt-4">
        <RankedTable
          rows={rows}
          isLoading={isLoading}
          errorMessage={errorMessage ?? undefined}
          onOpenInBuild={(row) => {
            // Open the per-strip futures_price_level primitive for the
            // selected (curve_family, contract_code) on the policy_futures
            // sub-agent.  The sibling primitive surfaces the per-strip
            // deep-dive the scanner ranks against.
            const ctx = encodeURIComponent(
              JSON.stringify({
                tools: [
                  {
                    tool: 'policy_futures_get_futures_price_level_tool',
                    params: {
                      curve_family: row.curve_family,
                      contract_code: row.contract_code,
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
          kind: 'Universe scanner (deterministic · multi-metric · STIR)',
          providers: ['TimescaleDB', 'macro_data.market_data_daily_enriched'],
          asOf: summary.asOfSpanEnd ?? undefined,
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
  scoreable,
  flagged,
  threshold,
}: {
  scoreable: number | null;
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
        <span className="text-[14px] text-fg-secondary">/ {scoreable ?? '—'}</span>
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
      <span className="kicker text-fg-muted">DISTRIBUTION (ALL METRICS)</span>
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

function CentralBankCard({
  rows,
}: {
  rows: ReadonlyArray<ScanPolicyFuturesExtremesResultRow>;
}) {
  // Surface the per-row central-bank + RFR/IBOR regime breakdown so the
  // desk sees at a glance which central-bank-anchored stems show up in the
  // top-N.  Required by ADR 0013 disclosure.
  const families = new Map<string, { flag: string; cb: string; regime: string }>();
  for (const r of rows) {
    if (families.has(r.curve_family)) continue;
    const meta = policyFuturesFamilyFor(r.curve_family);
    if (meta) {
      families.set(r.curve_family, {
        flag: meta.flag,
        cb: meta.centralBank,
        regime: meta.shortRateRegime,
      });
    }
  }
  return (
    <div className="card flex h-full flex-col gap-1.5 px-4 py-3">
      <span className="kicker text-fg-muted">CENTRAL-BANK REGIME (ADR 0013)</span>
      {families.size === 0 ? (
        <span className="text-[11.5px] text-fg-secondary">—</span>
      ) : (
        <ul className="flex flex-col gap-0.5 text-[11.5px] text-fg-secondary">
          {[...families.values()].map((meta, i) => (
            <li key={i} className="flex items-center gap-1.5">
              <span className="text-[13px] leading-none">{meta.flag}</span>
              <span className="flex-1 truncate">{meta.cb}</span>
              <span
                className={`inline-flex items-center rounded-sm px-1.5 py-[1px] font-mono text-[9.5px] tracking-wide ${
                  meta.regime === 'IBOR'
                    ? 'bg-amber-400/[0.08] text-amber-300 ring-1 ring-amber-400/30'
                    : 'bg-mint-400/[0.08] text-mint-300 ring-1 ring-mint-400/30'
                }`}
              >
                {meta.regime}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MetricBreakdownCard({
  summary,
}: {
  summary: ReturnType<typeof parseScanSummary>;
}) {
  // Surface the per-metric flagged-count breakdown so the desk can see at
  // a glance whether today's extremity is concentrated in IR (the headline
  // question), Δ (today's move), VOLUME (activity surge), or OI
  // (positioning shift).
  const entries = Object.entries(summary.perMetricFlagged) as Array<
    [keyof typeof METRIC_REGISTRY, number]
  >;
  return (
    <div className="card flex flex-col gap-2 px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="kicker text-fg-muted">PER-METRIC FLAGGED</span>
        <span className="text-[10.5px] text-fg-faint">
          {POLICY_FUTURES_SCANNER_COMPACT_CAVEAT}
        </span>
      </div>
      {entries.length === 0 ? (
        <span className="text-[11.5px] text-fg-secondary">—</span>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {entries.map(([metric, count]) => (
            <div
              key={metric}
              className="flex flex-col gap-0.5 rounded-md border border-line-subtle bg-surface-overlay px-3 py-2"
            >
              <span className="kicker text-fg-muted">
                {METRIC_REGISTRY[metric].scopeShort}
              </span>
              <span className="font-mono text-[18px] tabular-nums text-fg-primary">
                {count}
              </span>
              <span className="text-[10.5px] text-fg-faint">
                {METRIC_REGISTRY[metric].label}
              </span>
            </div>
          ))}
        </div>
      )}
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
                aria-label={`bin ${b.from} to ${b.to}: ${b.count} rows`}
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
  rows: ReadonlyArray<ScanPolicyFuturesExtremesResultRow>;
  isLoading?: boolean;
  errorMessage?: string;
  onOpenInBuild: (row: ScanPolicyFuturesExtremesResultRow) => void;
}) {
  return (
    <div className="card overflow-hidden">
      <div className="border-b border-line-subtle px-4 py-3">
        <span className="kicker text-fg-muted">Ranked detail (per metric)</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[960px] text-[12.5px]">
          <thead>
            <tr className="border-b border-line-subtle text-left text-[10.5px] uppercase tracking-wide text-fg-muted">
              <th className="px-4 py-2 font-normal">Rank</th>
              <th className="px-3 py-2 font-normal">Contract</th>
              <th className="px-3 py-2 font-normal">Pack</th>
              <th className="px-3 py-2 font-normal">Scope</th>
              <th className="px-3 py-2 font-normal">Regime</th>
              <th className="px-3 py-2 text-right font-normal">Value (native)</th>
              <th className="px-3 py-2 text-right font-normal">1D Δ</th>
              <th className="px-3 py-2 text-right font-normal">Z-Score (252d)</th>
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
                    <div className="h-3 w-14 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2">
                    <div className="h-3 w-12 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2">
                    <div className="h-3 w-10 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2">
                    <div className="h-3 w-10 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="ml-auto h-3 w-16 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="ml-auto h-3 w-12 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="ml-auto h-3 w-12 animate-pulse rounded bg-line-subtle" />
                  </td>
                  <td className="px-4 py-2 text-right">
                    <div className="ml-auto h-3 w-16 animate-pulse rounded bg-line-subtle" />
                  </td>
                </tr>
              ))
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-fg-secondary">
                  No STIR stems passed the threshold for this scan.
                </td>
              </tr>
            ) : (
              rows.map((row) => {
                const tone = zScoreTone(row.z_score);
                const metricMeta = METRIC_REGISTRY[row.metric];
                const narrative = narrativeTag(row.metric, row.signal);
                const pack = stripPack(row);
                return (
                  <tr
                    key={`${row.curve_family}-${row.contract_code}-${row.metric}-${row.rank}`}
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
                        <span className="font-mono text-[12px]">{row.contract_code}</span>
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <PackChip pack={pack} />
                    </td>
                    <td className="px-3 py-2">
                      <span className="inline-flex items-center rounded-sm bg-surface-overlay px-1.5 py-[1px] font-mono text-[10.5px] uppercase tracking-wide text-fg-secondary">
                        {metricMeta.scopeShort}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <RegimeChip regime={row.short_rate_regime} />
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex flex-col items-end leading-tight">
                        <span className="font-mono tabular-nums text-fg-primary">
                          {nativeValue(row)}
                        </span>
                        <span className="text-[10.5px] text-fg-muted">
                          {nativeUnits(row)}
                          {narrative && (
                            <>
                              <span className="text-fg-faint" aria-hidden> · </span>
                              <span>{narrative}</span>
                            </>
                          )}
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-fg-secondary">
                      {dailyChangeDisplay(row)}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${toneTextClass(tone)}`}>
                      {formatZScore(row.z_score)}
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

function PackChip({ pack }: { pack: 'WHITES' | 'REDS' }) {
  return (
    <span
      className={`inline-flex items-center rounded-sm px-1.5 py-[1px] font-mono text-[10px] uppercase tracking-wide ${
        pack === 'WHITES'
          ? 'bg-white/[0.05] text-fg-secondary ring-1 ring-line-soft'
          : 'bg-coral-400/[0.10] text-coral-300 ring-1 ring-coral-400/25'
      }`}
    >
      {pack}
    </span>
  );
}

function RegimeChip({ regime }: { regime: 'RFR' | 'IBOR' }) {
  return (
    <span
      className={`inline-flex items-center rounded-sm px-1.5 py-[1px] font-mono text-[10px] uppercase tracking-wide ring-1 ${
        regime === 'IBOR'
          ? 'bg-amber-400/[0.08] text-amber-300 ring-amber-400/30'
          : 'bg-mint-400/[0.08] text-mint-300 ring-mint-400/30'
      }`}
    >
      {regime}
    </span>
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
          Click <span className="font-semibold text-fg-primary">Open in Build</span> on any row to drill into the per-strip
          <code className="mx-1 rounded bg-bg-surface px-1 font-mono text-[10.5px]">
            policy_futures_get_futures_price_level_tool
          </code>
          with the selected (curve_family, contract_code) bound.
        </span>
      </div>
    </div>
  );
}
