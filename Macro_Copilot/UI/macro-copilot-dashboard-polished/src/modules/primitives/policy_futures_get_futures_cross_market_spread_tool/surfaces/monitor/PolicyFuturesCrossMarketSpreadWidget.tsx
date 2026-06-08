// ============================================================================
// PolicyFuturesCrossMarketSpreadWidget — Monitor bento tile for one
// matched-strip cross-market STIR spread between TWO different
// policy-futures curve_family values at ONE strip position (e.g. SOFR_FUT
// vs SONIA_FUT strip 1 = SFR1 − SFI1, SOFR_FUT vs EUR_SHORT_RATE_FUT
// strip 4 = SFR4 − ER4).
// ----------------------------------------------------------------------------
// Parameterised on (curve_family_a, curve_family_b, strip_position,
// lookback_days).  Cross-market invariant ``curve_family_a !=
// curve_family_b`` enforced at fetch-param resolution so the widget
// can't dispatch a self-spread against the schema layer.  Fetches the
// SAME typed-detail endpoint (/api/v1/rates/detail/policy-futures-cross-
// market) the Build views use — the standalone-bridge contract
// (methodology_exposure.md §5.4) ensures one endpoint per tool feeds
// every surface.
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// Mirrors the sibling PolicyFuturesCalendarSpreadWidget shape adapted
// for the cross-market Output (wire reports PERCENT POINTS in the
// A − B convention; the widget scales by 100 for the bps headline but
// preserves the A − B orientation — desk-canonical for cross-CB
// divergence).  The wire's ``methodology_disclosure`` rides on the
// title= tooltip so YAML edits flow to the Monitor tile too.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailPolicyFuturesCrossMarket,
  type FuturesCrossMarketSpreadDetailParams,
} from '@/services/ratesApi';
import type { FuturesCrossMarketSpreadOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';
import {
  crossCBLabel,
  curveMetaFor,
  isMixedRegime,
  pairStripLabel,
  pctToBps,
} from '../futuresCrossMarketSpreadShared';

type Props = {
  params: Record<string, unknown>;
};

export function PolicyFuturesCrossMarketSpreadWidget({ params }: Props) {
  const fetchParams = useMemo<FuturesCrossMarketSpreadDetailParams | null>(() => {
    const cfA =
      (typeof params.curve_family_a === 'string' && params.curve_family_a) || null;
    const cfB =
      (typeof params.curve_family_b === 'string' && params.curve_family_b) || null;
    if (!cfA || !cfB || cfA === cfB) return null;
    if (!curveMetaFor(cfA) || !curveMetaFor(cfB)) return null;

    const stripRaw = params.strip_position;
    const stripPosition =
      typeof stripRaw === 'string'
        ? Number(stripRaw)
        : typeof stripRaw === 'number'
          ? stripRaw
          : 1;
    if (!Number.isFinite(stripPosition) || stripPosition < 1) return null;

    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return {
      curve_family_a: cfA,
      curve_family_b: cfB,
      strip_position: stripPosition,
      lookback_days: lookback,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailPolicyFuturesCrossMarket(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete or both legs identical." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const aMeta = curveMetaFor(m.curve_family_a);
  const bMeta = curveMetaFor(m.curve_family_b);
  // Wire spread is in PERCENT POINTS in the A − B convention; scale by
  // 100 for the bps headline (NO sign flip — A − B IS the desk-canonical
  // cross-CB divergence direction).
  const spreadBps = pctToBps(m.spread_value_pct) ?? 0;
  const dailyBps = pctToBps(m.daily_change_spread_value_pct);
  const highBps = pctToBps(m.high_252d_spread_value_pct);
  const lowBps = pctToBps(m.low_252d_spread_value_pct);
  const z = m.z_score_spread;
  const dailyNeg = (dailyBps ?? 0) < 0;
  const zAbs = Math.abs(z ?? 0);
  const flagPair = aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : '';
  const pairDisplay = pairStripLabel(
    m.curve_family_a,
    m.curve_family_b,
    m.strip_position,
  );
  const cbPair = crossCBLabel(m.curve_family_a, m.curve_family_b);
  const mixedRegime = isMixedRegime(m.short_rate_regime_a, m.short_rate_regime_b);

  return (
    <>
      <WidgetHeader
        kicker={`${pairDisplay} ${flagPair}`.trim()}
        title={`${cbPair} Cross-CB STIR`}
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
            {spreadBps >= 0 ? '+' : ''}
            {spreadBps.toFixed(1)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">bp</span>
          <span className="font-mono text-[10px] text-fg-faint">
            ({m.spread_value_pct >= 0 ? '+' : ''}
            {m.spread_value_pct.toFixed(2)}%)
          </span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px]">
          <span>
            <span className="text-fg-faint">Δ1d </span>
            <span
              className={cn(
                'font-medium',
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

        {highBps != null && lowBps != null && (
          <div className="mt-1 flex items-center gap-2 font-mono text-[9.5px] text-fg-faint">
            <span>L {lowBps.toFixed(0)}</span>
            <div className="relative h-1.5 flex-1 rounded-full bg-white/[0.04] ring-1 ring-line-subtle">
              {(() => {
                const range = highBps - lowBps;
                if (range === 0) return null;
                const pct = ((spreadBps - lowBps) / range) * 100;
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
            in compact contexts; this is the load-bearing one-liner.  Use
            the wire's verbatim ``methodology_disclosure`` as the title=
            tooltip so YAML edits + the mixed-regime call-out flow to the
            Monitor tile too. */}
        <div
          className="font-mono text-[9.5px] leading-snug text-fg-faint"
          title={data.methodology_disclosure}
        >
          {mixedRegime
            ? `${cbPair} divergence (mixed regime). Inverse-priced STIR.`
            : `${cbPair} divergence on implied rates. Inverse-priced STIR.`}
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="policy_futures_get_futures_cross_market_spread_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn on parent re-renders.

function useFetchDetailPolicyFuturesCrossMarket(
  params: FuturesCrossMarketSpreadDetailParams | null,
) {
  const [state, setState] = useState<{
    data: FuturesCrossMarketSpreadOutput | null;
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
    fetchDetailPolicyFuturesCrossMarket(params)
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
