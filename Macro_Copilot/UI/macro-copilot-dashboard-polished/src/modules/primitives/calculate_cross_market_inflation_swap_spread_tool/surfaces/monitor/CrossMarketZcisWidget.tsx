// ============================================================================
// CrossMarketZcisWidget — Monitor bento tile for one same-tenor cross-market
// ZCIS spread (e.g. USD-EUR 5Y, USD-GBP 10Y, EUR-GBP 5Y).
// ----------------------------------------------------------------------------
// Parameterised on (leg_a, leg_b, tenor, lookback_days).  Cross-market
// invariant — leg_a != leg_b — enforced at fetch-param resolution so the
// widget can't dispatch an invalid pair against the schema layer.
// Fetches the SAME typed-detail endpoint (/api/v1/rates/detail/cross-market-zcis)
// the Build views use — the standalone-bridge contract
// (methodology_exposure.md §5.4).
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// The load-bearing index-family caveat (USD_ZCIS / EUR_ZCIS / GBP_ZCIS
// reference different inflation indices) surfaces via the kicker pair
// label + the wire's structured ``index_family_caveat`` line.
// Mirrors the BreakevenButterflyWidget shape.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailCrossMarketZcis,
  type CrossMarketZcisDetailParams,
} from '@/services/ratesApi';
import type { CrossMarketInflationSwapSpreadOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import {
  pairShortLabel,
  shortIndexCaveat,
  zcisFamilyFor,
} from '../crossMarketZcisShared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

export function CrossMarketZcisWidget({ params }: Props) {
  const fetchParams = useMemo<CrossMarketZcisDetailParams | null>(() => {
    const legA =
      typeof params.leg_a_curve_family === 'string'
        ? params.leg_a_curve_family
        : null;
    const legB =
      typeof params.leg_b_curve_family === 'string'
        ? params.leg_b_curve_family
        : null;
    const tenor = typeof params.tenor === 'string' ? params.tenor : null;
    if (!legA || !legB || legA === legB || !tenor) return null;

    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return {
      leg_a_curve_family: legA,
      leg_b_curve_family: legB,
      tenor,
      lookback_days: lookback,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailCrossMarketZcis(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete or both legs identical." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const spreadBps = m.spread_bps;
  const dailyNeg = (m.change_1d_bps ?? 0) < 0;
  const zAbs = Math.abs(m.z_score_252d ?? 0);
  const aMeta = zcisFamilyFor(m.leg_a_curve_family);
  const bMeta = zcisFamilyFor(m.leg_b_curve_family);
  const pairLabel = pairShortLabel(m.leg_a_curve_family, m.leg_b_curve_family);
  const flags = aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : '';
  const caveatLine = m.index_family_caveat
    ?? shortIndexCaveat(m.leg_a_curve_family, m.leg_b_curve_family);

  return (
    <>
      <WidgetHeader
        kicker={`${pairLabel} · ${m.tenor.toUpperCase()} ZCIS ${flags}`.trim()}
        title="Cross-Market ZCIS"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              zAbs >= 1.5
                ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
          >
            z {m.z_score_252d?.toFixed(2) ?? '—'}
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
          <span className="font-mono text-[10px] text-fg-faint">
            ({m.spread_pct >= 0 ? '+' : ''}
            {m.spread_pct.toFixed(2)}%)
          </span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px]">
          <span>
            <span className="text-fg-faint">Δ1d </span>
            <span
              className={cn('font-medium', dailyNeg ? 'text-mint-300' : 'text-coral-300')}
            >
              {m.change_1d_bps !== null
                ? `${m.change_1d_bps > 0 ? '+' : ''}${m.change_1d_bps.toFixed(1)} bp`
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
            in compact contexts; this is the load-bearing index-family caveat
            in a single line.  Sourced from the wire — not a TS literal. */}
        <div className="font-mono text-[9.5px] leading-snug text-fg-faint">
          {caveatLine}
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_cross_market_inflation_swap_spread_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn.
function useFetchDetailCrossMarketZcis(
  params: CrossMarketZcisDetailParams | null,
) {
  const [state, setState] = useState<{
    data: CrossMarketInflationSwapSpreadOutput | null;
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
    fetchDetailCrossMarketZcis(params)
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
