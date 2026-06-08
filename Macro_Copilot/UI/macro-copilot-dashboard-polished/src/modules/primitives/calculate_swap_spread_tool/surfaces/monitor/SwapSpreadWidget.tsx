// ============================================================================
// SwapSpreadWidget — Monitor bento tile for one cross-domain swap spread
// (e.g. UST 10Y vs USD_SOFR_OIS 10Y, BUND 10Y vs EUR_ESTR_OIS 10Y).
// ----------------------------------------------------------------------------
// Parameterised on (pair, tenor, lookback_days) where ``pair`` is the
// sovereign curve_family that uniquely determines the canonical OIS counterpart
// by currency (a swap spread is desk-meaningful only within a single currency
// — the backend schema rejects cross-currency pairs).  Fetches the SAME
// typed-detail endpoint (/api/v1/rates/detail/swap-spread) the Build views use
// — the standalone-bridge contract (methodology_exposure.md §5.4).
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.  The
// load-bearing par-leg OIS approximation caveat surfaces via the kicker pair
// label + the desk-canonical one-liner in the footer.  Mirrors the OIS
// cross-market / breakeven widget shapes.  Shows current spread (bps + sign
// caption — "Treasuries Cheap" / "Treasuries Rich"), 1d change, z-score
// badge, AND a 252d range strip with a current marker (the swap-spread wire
// DOES carry high_252d_bps / low_252d_bps — same shape as the OIS
// cross-market sibling, so the range strip ships).
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailSwapSpread,
  type SwapSpreadDetailParams,
} from '@/services/ratesApi';
import type { SwapSpreadOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import {
  SWAP_SPREAD_COMPACT_CAVEAT,
  pairForSovereign,
  signCaption,
} from '../swapSpreadShared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

export function SwapSpreadWidget({ params }: Props) {
  const fetchParams = useMemo<SwapSpreadDetailParams | null>(() => {
    // ``pair`` is the sovereign family; fall back to explicit
    // sovereign_curve_family + ois_curve_family params for deep-links.
    const sovereign =
      (typeof params.pair === 'string' && params.pair)
      || (typeof params.sovereign_curve_family === 'string'
        && params.sovereign_curve_family)
      || null;
    const t = typeof params.tenor === 'string' ? params.tenor : null;
    if (!sovereign || !t) return null;
    const meta = pairForSovereign(sovereign);
    if (!meta) return null;
    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return {
      sovereign_curve_family: meta.sovereignFamily,
      ois_curve_family: meta.oisFamily,
      tenor: t,
      lookback_days: lookback,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailSwapSpread(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const spreadBps = m.current_spread_bps;
  const dailyNeg = (m.daily_change_bps ?? 0) < 0;
  const zAbs = Math.abs(m.current_z_score ?? 0);
  const pair = pairForSovereign(m.sovereign_curve_family);
  const flag = pair?.flag ?? '';
  const pairLabel = pair
    ? `${pair.sovereignShort}-${pair.oisShort}`
    : `${m.sovereign_curve_family}-${m.ois_curve_family}`;
  const caption = signCaption(spreadBps);

  return (
    <>
      <WidgetHeader
        kicker={`${pairLabel} · ${m.tenor.toUpperCase()} ${flag}`.trim()}
        title="Swap Spread"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              zAbs >= 1.5
                ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
          >
            z {m.current_z_score?.toFixed(2) ?? '—'}
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[28px] font-light leading-none tracking-[-0.012em] text-fg-primary">
            {spreadBps >= 0 ? '+' : ''}
            {spreadBps.toFixed(1)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">bp</span>
          <span className="font-mono text-[10px] text-fg-faint">{caption}</span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px]">
          <span>
            <span className="text-fg-faint">Δ1d </span>
            <span
              className={cn('font-medium', dailyNeg ? 'text-mint-300' : 'text-coral-300')}
            >
              {m.daily_change_bps !== null
                ? `${m.daily_change_bps > 0 ? '+' : ''}${m.daily_change_bps.toFixed(1)} bp`
                : '—'}
            </span>
          </span>
          <span className="text-fg-faint">·</span>
          <span>
            <span className="text-fg-faint">%ile </span>
            <span className="font-medium text-fg-secondary">
              {m.percentile_252d?.toFixed(0) ?? '—'}
            </span>
          </span>
        </div>

        {m.high_252d_bps != null && m.low_252d_bps != null && (
          <div className="mt-1 flex items-center gap-2 font-mono text-[9.5px] text-fg-faint">
            <span>L {m.low_252d_bps.toFixed(0)}</span>
            <div className="relative h-1.5 flex-1 rounded-full bg-white/[0.04] ring-1 ring-line-subtle">
              {(() => {
                const range = m.high_252d_bps - m.low_252d_bps;
                if (range === 0) return null;
                const pct = ((spreadBps - m.low_252d_bps) / range) * 100;
                return (
                  <span
                    aria-hidden
                    className="absolute top-1/2 h-3 w-[2px] -translate-y-1/2 rounded-full bg-ice-300 shadow-[0_0_6px_rgba(122,162,255,0.6)]"
                    style={{ left: `calc(${Math.min(100, Math.max(0, pct))}% - 1px)` }}
                  />
                );
              })()}
            </div>
            <span>H {m.high_252d_bps.toFixed(0)}</span>
          </div>
        )}

        {/* Per rendering_density.md §2.2 + §12 methodology MUST be reachable
            in compact contexts; this is the load-bearing par-leg OIS
            approximation caveat in a single line.  The full honesty
            disclosure lives on the Extended view's methodology card. */}
        <div className="font-mono text-[9.5px] leading-snug text-fg-faint">
          {SWAP_SPREAD_COMPACT_CAVEAT}
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_swap_spread_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn.
function useFetchDetailSwapSpread(params: SwapSpreadDetailParams | null) {
  const [state, setState] = useState<{
    data: SwapSpreadOutput | null;
    error: Error | null;
    isLoading: boolean;
  }>({ data: null, error: null, isLoading: !!params });

  const paramsKey = params ? JSON.stringify(params) : null;

  useEffect(() => {
    if (!params) {
      setState({ data: null, error: null, isLoading: false });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, isLoading: true, error: null }));
    fetchDetailSwapSpread(params)
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
