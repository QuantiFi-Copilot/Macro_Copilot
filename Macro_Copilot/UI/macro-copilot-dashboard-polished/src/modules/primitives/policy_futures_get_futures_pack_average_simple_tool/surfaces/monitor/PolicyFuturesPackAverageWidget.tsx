// ============================================================================
// PolicyFuturesPackAverageWidget — Monitor bento tile for one STIR
// pack-average implied rate (arithmetic mean across four consecutive
// quarterly STIR contracts on ONE policy-futures curve family).
// ----------------------------------------------------------------------------
// Parameterised on (curve_family, pack, lookback_days).  Fetches the
// SAME typed-detail endpoint (/api/v1/rates/detail/policy-futures-pack-
// average) the Build views use — the standalone-bridge contract
// (methodology_exposure.md §5.4) ensures one endpoint per tool feeds
// every surface.
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// Mirrors the sibling PolicyFuturesCrossMarketSpreadWidget shape adapted
// for the pack-average Output (wire reports the level in PERCENT and the
// 1d change in PERCENT POINTS; the widget renders the level in PERCENT
// for the headline and scales the 1d change by 100 for the bps subtext).
// The wire's ``methodology_disclosure`` rides on the title= tooltip so
// YAML edits flow to the Monitor tile too.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailPolicyFuturesPackAverage,
  type FuturesPackAverageSimpleDetailParams,
} from '@/services/ratesApi';
import type { FuturesPackAverageSimpleOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';
import { curveMetaFor, pctToBps } from '../futuresPackAverageSimpleShared';

type Props = {
  params: Record<string, unknown>;
};

export function PolicyFuturesPackAverageWidget({ params }: Props) {
  const fetchParams = useMemo<FuturesPackAverageSimpleDetailParams | null>(() => {
    const cf =
      (typeof params.curve_family === 'string' && params.curve_family) || null;
    const pack = (typeof params.pack === 'string' && params.pack) || null;
    if (!cf || !pack) return null;
    if (!curveMetaFor(cf)) return null;
    if (pack !== 'whites' && pack !== 'reds') return null;

    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return {
      curve_family: cf,
      pack,
      lookback_days: lookback,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailPolicyFuturesPackAverage(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const meta = curveMetaFor(m.curve_family);
  const dailyBps = pctToBps(m.daily_change_pack_average_implied_rate_pct);
  const z = m.z_score_pack_average;
  const dailyNeg = (dailyBps ?? 0) < 0;
  const zAbs = Math.abs(z ?? 0);
  const flag = meta?.flag ?? '';
  const shortLabel = meta?.shortLabel ?? m.curve_family;
  const packLabel = m.pack.toUpperCase();

  return (
    <>
      <WidgetHeader
        kicker={`${shortLabel.toUpperCase()} · ${packLabel} ${flag}`.trim()}
        title="Pack Average"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              zAbs >= 1.5
                ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
          >
            z {z?.toFixed(2) ?? '—'}
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[28px] font-light leading-none tracking-[-0.012em] text-fg-primary">
            {m.pack_average_implied_rate_pct >= 0 ? '+' : ''}
            {m.pack_average_implied_rate_pct.toFixed(3)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">%</span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px]">
          <span>
            <span className="text-fg-faint">Δ1d </span>
            <span
              className={cn(
                'font-medium',
                // Convention: positive change = pack repriced HIGHER
                // = hawkish implied-policy-path stretch → coral.
                dailyNeg ? 'text-mint-300' : 'text-coral-300',
              )}
            >
              {dailyBps != null
                ? `${dailyBps > 0 ? '+' : ''}${dailyBps.toFixed(1)} bp`
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

        {m.high_252d_pack_average_implied_rate_pct != null
          && m.low_252d_pack_average_implied_rate_pct != null && (
          <div className="mt-1 flex items-center gap-2 font-mono text-[9.5px] text-fg-faint">
            <span>L {m.low_252d_pack_average_implied_rate_pct.toFixed(2)}</span>
            <div className="relative h-1.5 flex-1 rounded-full bg-white/[0.04] ring-1 ring-line-subtle">
              {(() => {
                const high = m.high_252d_pack_average_implied_rate_pct as number;
                const low = m.low_252d_pack_average_implied_rate_pct as number;
                const range = high - low;
                if (range === 0) return null;
                const pct =
                  ((m.pack_average_implied_rate_pct - low) / range) * 100;
                return (
                  <span
                    aria-hidden
                    className="absolute top-1/2 h-3 w-[2px] -translate-y-1/2 rounded-full bg-ice-300 shadow-[0_0_6px_rgba(122,162,255,0.6)]"
                    style={{ left: `calc(${Math.min(100, Math.max(0, pct))}% - 1px)` }}
                  />
                );
              })()}
            </div>
            <span>H {m.high_252d_pack_average_implied_rate_pct.toFixed(2)}</span>
          </div>
        )}

        {/* Per rendering_density.md §2.2 + §12 methodology MUST be reachable
            in compact contexts; this is the load-bearing one-liner.  Use
            the wire's verbatim ``methodology_disclosure`` as the title=
            tooltip so YAML edits + the arithmetic-mean weighting + the
            refusal of duration-weighted variants flow to the Monitor tile too. */}
        <div
          className="font-mono text-[9.5px] leading-snug text-fg-faint"
          title={data.methodology_disclosure}
        >
          Simple mean × 4 contracts · 100-minus-rate STIR convention.
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="policy_futures_get_futures_pack_average_simple_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn on parent re-renders.

function useFetchDetailPolicyFuturesPackAverage(
  params: FuturesPackAverageSimpleDetailParams | null,
) {
  const [state, setState] = useState<{
    data: FuturesPackAverageSimpleOutput | null;
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
    fetchDetailPolicyFuturesPackAverage(params)
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
