// ============================================================================
// PolicyFuturesPriceLevelWidget — Monitor bento tile for one STIR strip slot.
// ----------------------------------------------------------------------------
// Parameterised on (curve_family, strip_position, lookback_days).  Fetches
// the SAME typed-detail endpoint (/api/v1/rates/detail/policy-futures-price)
// the extended + compact Build views use — the standalone-bridge contract
// per methodology_exposure.md §5.4 ensures one endpoint per tool feeds
// every surface.
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT —
// no separate compact/extended split like Build has.  This widget renders
// at small/medium widget sizes inside the Monitor bento grid.
//
// Mirrors the sibling sovereign yield_level / linker real_yield_level
// monitor tiles: kicker (stem · pack), title, z-score badge, headline
// number (IMPLIED RATE in PERCENT), 1d change (bps) + 252d percentile,
// and a 252d range strip with a current marker on the implied-rate axis.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailPolicyFuturesPrice,
  type PolicyFuturesPriceDetailParams,
} from '@/services/ratesApi';
import type { PolicyFuturesPriceLevelOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';
import {
  curveMetaFor,
  impliedRateChangeBps,
  stripPackLabel,
  stripStemLabel,
} from '../policyFuturesPriceShared';

type Props = {
  params: Record<string, unknown>;
};

export function PolicyFuturesPriceLevelWidget({ params }: Props) {
  const fetchParams = useMemo<PolicyFuturesPriceDetailParams | null>(() => {
    const cf = typeof params.curve_family === 'string' ? params.curve_family : null;
    const sp =
      typeof params.strip_position === 'string'
        ? Number(params.strip_position)
        : typeof params.strip_position === 'number'
          ? params.strip_position
          : null;
    if (!cf || !sp || !Number.isFinite(sp)) return null;
    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return { curve_family: cf, strip_position: sp, lookback_days: lookback };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailPolicyFuturesPrice(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const meta = curveMetaFor(m.curve_family);
  const dailyBps = impliedRateChangeBps(m.daily_change_implied_rate_pct);
  const dailyTighter = (dailyBps ?? 0) > 0;
  const z = m.z_score_implied_rate ?? 0;
  const zAbs = Math.abs(z);

  const stem = stripStemLabel(m.curve_family, m.strip_position);
  const packLabel = stripPackLabel(m.curve_family, m.strip_position);
  const flag = meta?.flag ?? '';

  return (
    <>
      <WidgetHeader
        kicker={`${stem.toUpperCase()} · ${packLabel.toUpperCase()} ${flag}`.trim()}
        title="Policy Futures Price"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              zAbs >= 1.5
                ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
          >
            z {m.z_score_implied_rate?.toFixed(2) ?? '—'}
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span
            className={cn(
              'font-mono text-[28px] font-light leading-none tracking-[-0.012em]',
              // Implied rate level — neutral; change + z below carry tone.
              'text-fg-primary',
            )}
          >
            {m.implied_rate_pct >= 0 ? '+' : ''}
            {m.implied_rate_pct.toFixed(2)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">%</span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px]">
          <span>
            <span className="text-fg-faint">Δ1d </span>
            <span
              className={cn(
                'font-medium',
                // Positive bps = implied rate UP = tightening → coral.
                // Mirrors the sovereign yield_levels widget tone semantics.
                dailyTighter ? 'text-coral-300' : 'text-mint-300',
              )}
            >
              {dailyBps != null
                ? `${dailyBps > 0 ? '+' : ''}${dailyBps.toFixed(1)} bps`
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
          <span className="text-fg-faint">·</span>
          <span>
            <span className="text-fg-faint">Px </span>
            <span className="font-medium text-fg-secondary">
              {m.raw_price.toFixed(2)}
            </span>
          </span>
        </div>

        {/* 252d implied-rate range strip with a current marker — mirrors
            the sovereign yield_levels widget visual cue. */}
        {m.high_252d_implied_rate_pct != null && m.low_252d_implied_rate_pct != null && (
          <div className="mt-1 flex items-center gap-2 font-mono text-[9.5px] text-fg-faint">
            <span>L {m.low_252d_implied_rate_pct.toFixed(2)}</span>
            <div className="relative h-1.5 flex-1 rounded-full bg-white/[0.04] ring-1 ring-line-subtle">
              {(() => {
                const range =
                  m.high_252d_implied_rate_pct - m.low_252d_implied_rate_pct;
                if (range === 0) return null;
                const pct =
                  ((m.implied_rate_pct - m.low_252d_implied_rate_pct) / range) * 100;
                return (
                  <span
                    aria-hidden
                    className="absolute top-1/2 h-3 w-[2px] -translate-y-1/2 rounded-full bg-ice-300 shadow-[0_0_6px_rgba(122,162,255,0.6)]"
                    style={{ left: `calc(${Math.min(100, Math.max(0, pct))}% - 1px)` }}
                  />
                );
              })()}
            </div>
            <span>H {m.high_252d_implied_rate_pct.toFixed(2)}</span>
          </div>
        )}
      </WidgetBody>
      <WidgetProvenance
        toolName="policy_futures_get_futures_price_level_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — mirrors useFetchDetailRealYield in the linker analog.
// Stable JSON key prevents re-fetch churn on parent re-renders.

function useFetchDetailPolicyFuturesPrice(
  params: PolicyFuturesPriceDetailParams | null,
) {
  const [state, setState] = useState<{
    data: PolicyFuturesPriceLevelOutput | null;
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
    fetchDetailPolicyFuturesPrice(params)
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
