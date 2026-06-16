// ============================================================================
// BondFuturesScannerWidget — Monitor bento tile for the universe-wide bond-
// futures extremes scanner.
// ----------------------------------------------------------------------------
// Parameterised on (scope, top_n, min_abs_z_score) where ``scope`` is the
// bond-futures curve_family or the magic "ALL" value for the full universe.
// Fetches the SAME typed-detail endpoint the Build views use —
// /api/v1/rates/detail/bond-futures-scanner — per the standalone-bridge
// contract (methodology_exposure.md §5.4).
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// The tile surfaces the desk-canonical "where is the bond-futures universe
// stretched today?" read — a flagged-count chip, the top-3 cross-metric
// extremes (sorted by |z| across all four metrics), and the load-bearing
// V1-monitors-only / CTD-out-of-scope caveat from ADR 0013.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailBondFuturesScanner,
  type BondFuturesScannerDetailParams,
} from '@/services/ratesApi';
import type { ScanBondFuturesExtremesOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetError, WidgetLoading } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';
import {
  BOND_FUTURES_SCANNER_COMPACT_CAVEAT,
  METRIC_REGISTRY,
  formatZScore,
  instrumentFlag,
  parseScanSummary,
  sortRowsByAbsZScore,
  zScoreTone,
} from '../bondFuturesScannerShared';

type Props = {
  params: Record<string, unknown>;
};

const SCOPE_ALL = 'ALL';

export function BondFuturesScannerWidget({ params }: Props) {
  const fetchParams = useMemo<BondFuturesScannerDetailParams>(() => {
    const scopeRaw = typeof params.scope === 'string' ? params.scope : SCOPE_ALL;
    const topNRaw = params.top_n;
    const minAbsZRaw = params.min_abs_z_score;
    const topN =
      typeof topNRaw === 'string'
        ? Number(topNRaw)
        : typeof topNRaw === 'number'
          ? topNRaw
          : undefined;
    const minAbsZ =
      typeof minAbsZRaw === 'string'
        ? Number(minAbsZRaw)
        : typeof minAbsZRaw === 'number'
          ? minAbsZRaw
          : undefined;
    return {
      curve_families: scopeRaw === SCOPE_ALL ? undefined : scopeRaw,
      top_n: Number.isFinite(topN) ? topN : undefined,
      min_abs_z_score: Number.isFinite(minAbsZ) ? minAbsZ : undefined,
      as_of_date: typeof params.as_of_date === 'string' ? params.as_of_date : undefined,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailBondFuturesScanner(fetchParams);

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const summary = parseScanSummary(data.scan_summary);
  const sorted = sortRowsByAbsZScore(data.results);
  const topRows = sorted.slice(0, 3);
  const flagged = summary.flaggedTotal ?? sorted.length;
  const scoreable = summary.scoreable;
  const flaggedAtExtremity = sorted.filter(
    (r) => Math.abs(r.z_score ?? 0) >= 2.0,
  ).length;
  const scopeLabel =
    fetchParams.curve_families && fetchParams.curve_families !== SCOPE_ALL
      ? fetchParams.curve_families
      : 'UST / Bund / Gilt / JGB / ...';

  return (
    <>
      <WidgetHeader
        kicker={`BOND FUT · ${scopeLabel}`}
        title="Bond Futures Extremes"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              flaggedAtExtremity > 0
                ? 'ring-coral-400/30 bg-coral-400/[0.08] text-coral-300'
                : flagged > 0
                  ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                  : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
          >
            {flagged} flagged
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[28px] font-light leading-none tracking-[-0.012em] text-fg-primary">
            {flagged}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">
            of {scoreable ?? '—'} scoreable
          </span>
        </div>
        <div className="font-mono text-[10.5px] text-fg-faint">
          Threshold {summary.threshold != null ? `|z| ≥ ${summary.threshold.toFixed(1)}σ` : 'YAML default'}
          {' · '}across price · Δ · vol · OI
        </div>

        {topRows.length === 0 ? (
          <div className="font-mono text-[10.5px] text-fg-secondary">
            No stems passed the threshold.
          </div>
        ) : (
          <div className="mt-1 flex flex-col gap-1">
            {topRows.map((row, idx) => {
              const tone = zScoreTone(row.z_score);
              const toneClass =
                tone === 'extreme-up'
                  ? 'text-coral-300'
                  : tone === 'extreme-down'
                    ? 'text-mint-300'
                    : tone === 'elevated'
                      ? 'text-amber-300'
                      : tone === 'positive'
                        ? 'text-mint-300'
                        : tone === 'negative'
                          ? 'text-coral-300'
                          : 'text-fg-secondary';
              const scope = METRIC_REGISTRY[row.metric].scopeShort;
              return (
                <div
                  key={`${row.curve_family}-${row.contract_code}-${row.metric}-${row.rank}`}
                  className="grid grid-cols-[16px_1fr_auto_auto] items-center gap-2 font-mono text-[11px]"
                >
                  <span className="text-fg-faint">{idx + 1}</span>
                  <span className="flex items-center gap-1.5 text-fg-secondary">
                    {instrumentFlag(row) && (
                      <span className="text-[12px] leading-none">{instrumentFlag(row)}</span>
                    )}
                    <span className="truncate">{row.contract_code}</span>
                  </span>
                  <span className="text-right text-fg-muted uppercase tracking-wide text-[10px]">
                    {scope}
                  </span>
                  <span className={cn('text-right', toneClass)}>
                    {formatZScore(row.z_score)}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {/* Per rendering_density.md §2.2 + §12 methodology MUST be reachable
            in compact contexts; this is the load-bearing V1 monitors-only /
            CTD-out-of-scope caveat in a single line.  The full multi-line
            disclosure flows from the wire (data.methodology_disclosure)
            and is surfaced on the Extended view. */}
        <div
          className="font-mono text-[9.5px] leading-snug text-fg-faint"
          title={data.methodology_disclosure || BOND_FUTURES_SCANNER_COMPACT_CAVEAT}
        >
          {BOND_FUTURES_SCANNER_COMPACT_CAVEAT}
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="scan_bond_futures_extremes_tool"
        asOfDate={summary.asOfSpanEnd ?? undefined}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn.
function useFetchDetailBondFuturesScanner(params: BondFuturesScannerDetailParams) {
  const [state, setState] = useState<{
    data: ScanBondFuturesExtremesOutput | null;
    error: Error | null;
    isLoading: boolean;
  }>({ data: null, error: null, isLoading: true });

  const paramsKey = JSON.stringify(params);

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, isLoading: true, error: null }));
    fetchDetailBondFuturesScanner(params)
      .then((data) => {
        if (!cancelled) setState({ data, error: null, isLoading: false });
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setState({
            data: null,
            error: e instanceof Error ? e : new Error('fetch failed'),
            isLoading: false,
          });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramsKey]);

  return state;
}
