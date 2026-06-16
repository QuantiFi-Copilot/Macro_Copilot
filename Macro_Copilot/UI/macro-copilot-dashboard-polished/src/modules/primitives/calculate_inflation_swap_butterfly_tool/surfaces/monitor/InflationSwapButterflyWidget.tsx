// ============================================================================
// InflationSwapButterflyWidget — Monitor bento tile for one same-curve ZCIS
// butterfly (3-point curvature on a single ZCIS curve family).
// ----------------------------------------------------------------------------
// Parameterised on (curve, triplet, lookback_days) where ``curve`` is the
// ZCIS curve_family and ``triplet`` is the registered (short, belly, long)
// tuple selected by label (e.g. "2s5s10s").  Fetches the SAME typed-detail
// endpoint (/api/v1/rates/detail/zcis-butterfly) the Build views use —
// the standalone-bridge contract (methodology_exposure.md §5.4).
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// The CPI-family caveat (CPI-U / HICPxT / RPI not fungible) surfaces via
// the kicker pair label + the methodology card on the Extended view.
// Mirrors the CrossMarketZcisWidget shape, adapted for a single-curve
// input (no leg pair — one curve_family + three tenors).
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailInflationSwapButterfly,
  type InflationSwapButterflyDetailParams,
} from '@/services/ratesApi';
import type { InflationSwapButterflyOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import {
  INFLATION_SWAP_BUTTERFLY_COMPACT_CAVEAT,
  INFLATION_SWAP_BUTTERFLY_TRIPLETS_BY_CURVE,
  tripletHyphenLabel,
  zcisFamilyFor,
} from '../inflationSwapButterflyShared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

export function InflationSwapButterflyWidget({ params }: Props) {
  const fetchParams = useMemo<InflationSwapButterflyDetailParams | null>(() => {
    // ``curve`` is the ZCIS family; fall back to an explicit curve_family
    // param for direct deep-links.
    const curveFamily =
      (typeof params.curve === 'string' && params.curve)
      || (typeof params.curve_family === 'string' && params.curve_family)
      || null;
    if (!curveFamily) return null;
    const meta = zcisFamilyFor(curveFamily);
    if (!meta) return null;

    // The triplet selector value is the registered triplet label (e.g.
    // "2s5s10s").  Look up the (short, belly, long) tuple from the
    // per-curve registry so the widget can't dispatch an invalid ordering.
    const tripletKey = typeof params.triplet === 'string' ? params.triplet : null;
    const triplets =
      INFLATION_SWAP_BUTTERFLY_TRIPLETS_BY_CURVE[curveFamily] ?? [];
    const selected =
      (tripletKey != null && triplets.find((t) => t.label === tripletKey))
      || triplets[0];
    if (!selected) return null;

    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return {
      curve_family: meta.family,
      short_tenor: selected.short,
      belly_tenor: selected.belly,
      long_tenor: selected.long,
      lookback_days: lookback,
      as_of_date: typeof params.as_of_date === 'string' ? params.as_of_date : undefined,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailInflationSwapButterfly(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const flyBps = m.current_butterfly_bps ?? 0;
  const highBps = m.high_252d_bps;
  const lowBps = m.low_252d_bps;
  const dailyNeg = (m.daily_change_bps ?? 0) < 0;
  const zAbs = Math.abs(m.current_z_score ?? 0);
  const meta = zcisFamilyFor(m.curve_family);
  const flag = meta?.flag ?? '';
  const triplet = tripletHyphenLabel(m.short_tenor, m.belly_tenor, m.long_tenor);
  const curveLabel = meta
    ? `${meta.marketShort} ZCIS · ${meta.indexShort}`
    : m.curve_family;

  return (
    <>
      <WidgetHeader
        kicker={`${curveLabel} · ${triplet} ${flag}`.trim()}
        title="Inflation Swap Butterfly"
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
            {flyBps >= 0 ? '+' : ''}
            {flyBps.toFixed(1)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">bp</span>
          <span className="font-mono text-[10px] text-fg-faint">
            {flyBps > 0 ? 'belly cheap' : flyBps < 0 ? 'belly rich' : 'flat'}
          </span>
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

        {highBps != null && lowBps != null && (
          <div className="mt-1 flex items-center gap-2 font-mono text-[9.5px] text-fg-faint">
            <span>L {lowBps.toFixed(0)}</span>
            <div className="relative h-1.5 flex-1 rounded-full bg-white/[0.04] ring-1 ring-line-subtle">
              {(() => {
                const range = highBps - lowBps;
                if (range === 0) return null;
                const pct = ((flyBps - lowBps) / range) * 100;
                return (
                  <span
                    aria-hidden
                    className="absolute top-1/2 h-3 w-[2px] -translate-y-1/2 rounded-full bg-ice-300 shadow-[0_0_6px_rgba(122,162,255,0.6)]"
                    style={{ left: `calc(${Math.min(100, Math.max(0, pct))}% - 1px)` }}
                  />
                );
              })()}
            </div>
            <span>H {highBps.toFixed(0)}</span>
          </div>
        )}

        {/* Per rendering_density.md §2.2 + §12 methodology MUST be reachable
            in compact contexts; this is the load-bearing CPI-family caveat
            in a single line.  Static here (no per-curve specialisation —
            the full per-leg index-family disclosure lives on the Extended
            view's methodology card sourced from the wire). */}
        <div className="font-mono text-[9.5px] leading-snug text-fg-faint">
          {INFLATION_SWAP_BUTTERFLY_COMPACT_CAVEAT}
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_inflation_swap_butterfly_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn.
function useFetchDetailInflationSwapButterfly(
  params: InflationSwapButterflyDetailParams | null,
) {
  const [state, setState] = useState<{
    data: InflationSwapButterflyOutput | null;
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
    fetchDetailInflationSwapButterfly(params)
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
