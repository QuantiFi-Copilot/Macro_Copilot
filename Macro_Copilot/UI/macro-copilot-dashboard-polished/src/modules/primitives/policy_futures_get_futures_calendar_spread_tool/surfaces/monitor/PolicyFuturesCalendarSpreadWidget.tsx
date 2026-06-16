// ============================================================================
// PolicyFuturesCalendarSpreadWidget — Monitor bento tile for one same-curve
// STIR calendar spread (2-point implied-rate slope on a single
// policy-futures curve_family).
// ----------------------------------------------------------------------------
// Parameterised on (curve_family, pair, lookback_days) where ``pair`` is the
// registered (short, long) tuple selected by label (e.g. "1-2", "1-3",
// "1-4 (Whites)").  Fetches the SAME typed-detail endpoint
// (/api/v1/rates/detail/policy-futures-calendar) the Build views use — the
// standalone-bridge contract (methodology_exposure.md §5.4) ensures one
// endpoint per tool feeds every surface.
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// Mirrors the sibling PolicyFuturesButterflyWidget shape adapted for the
// calendar-spread Output (wire reports PERCENT POINTS in the FRONT − BACK
// convention; the widget flips sign and multiplies by 100 for the bps
// headline in the desk-recognised BACK − FRONT convention).
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailPolicyFuturesCalendar,
  type FuturesCalendarSpreadDetailParams,
} from '@/services/ratesApi';
import type { FuturesCalendarSpreadOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';
import {
  CALENDAR_PAIRS_BY_CURVE,
  calendarPairLabel,
  curveMetaFor,
  flipWireZScore,
  wirePctToDisplayBps,
} from '../futuresCalendarSpreadShared';

type Props = {
  params: Record<string, unknown>;
};

export function PolicyFuturesCalendarSpreadWidget({ params }: Props) {
  const fetchParams = useMemo<FuturesCalendarSpreadDetailParams | null>(() => {
    const cf =
      (typeof params.curve_family === 'string' && params.curve_family) || null;
    if (!cf) return null;
    const meta = curveMetaFor(cf);
    if (!meta) return null;

    // ``pair`` is the registered pair label (e.g. "1-3").  Look up the
    // (short, long) tuple from the per-curve registry so the widget
    // can't dispatch an invalid ordering.
    const pairKey =
      typeof params.pair === 'string' ? params.pair : null;
    const pairs = CALENDAR_PAIRS_BY_CURVE[cf] ?? [];
    const selected =
      (pairKey != null && pairs.find((p) => p.label === pairKey))
      || pairs[0];
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
      strip_position_short: selected.short,
      strip_position_long: selected.long,
      lookback_days: lookback,
      as_of_date: typeof params.as_of_date === 'string' ? params.as_of_date : undefined,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailPolicyFuturesCalendar(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const meta = curveMetaFor(m.curve_family);
  // Wire is FRONT − BACK in PERCENT POINTS; flip + scale to BACK − FRONT
  // in bps for the desk-canonical display convention (positive bps =
  // back rate higher = steeper policy path).
  const spreadBps = wirePctToDisplayBps(m.spread_implied_rate_pct) ?? 0;
  const dailyBps = wirePctToDisplayBps(m.daily_change_spread_implied_rate_pct);
  const highBpsDisplay = wirePctToDisplayBps(m.low_252d_spread_implied_rate_pct);
  const lowBpsDisplay = wirePctToDisplayBps(m.high_252d_spread_implied_rate_pct);
  const displayZ = flipWireZScore(m.z_score_spread_implied_rate);
  const displayPercentile =
    m.percentile_252d != null && Number.isFinite(m.percentile_252d)
      ? 100 - m.percentile_252d
      : null;
  const dailyNeg = (dailyBps ?? 0) < 0;
  const zAbs = Math.abs(displayZ ?? 0);
  const flag = meta?.flag ?? '';
  const pairDisplay = calendarPairLabel(
    m.strip_position_short,
    m.strip_position_long,
  );
  const curveLabel = meta
    ? `${meta.shortLabel} · ${pairDisplay}`
    : `${m.curve_family} ${pairDisplay}`;

  return (
    <>
      <WidgetHeader
        kicker={`${curveLabel} ${flag}`.trim()}
        title="STIR Calendar Spread"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              zAbs >= 1.5
                ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
          >
            z {displayZ?.toFixed(2) ?? '—'}
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
            {spreadBps > 0 ? 'back cheap' : spreadBps < 0 ? 'front cheap' : 'flat'}
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
              {displayPercentile?.toFixed(0) ?? '—'}
            </span>
          </span>
        </div>

        {highBpsDisplay != null && lowBpsDisplay != null && (
          <div className="mt-1 flex items-center gap-2 font-mono text-[9.5px] text-fg-faint">
            <span>L {lowBpsDisplay.toFixed(0)}</span>
            <div className="relative h-1.5 flex-1 rounded-full bg-white/[0.04] ring-1 ring-line-subtle">
              {(() => {
                const range = highBpsDisplay - lowBpsDisplay;
                if (range === 0) return null;
                const pct = ((spreadBps - lowBpsDisplay) / range) * 100;
                return (
                  <span
                    aria-hidden
                    className="absolute top-1/2 h-3 w-[2px] -translate-y-1/2 rounded-full bg-ice-300 shadow-[0_0_6px_rgba(122,162,255,0.6)]"
                    style={{ left: `calc(${Math.min(100, Math.max(0, pct))}% - 1px)` }}
                  />
                );
              })()}
            </div>
            <span>H {highBpsDisplay.toFixed(0)}</span>
          </div>
        )}

        {/* Per rendering_density.md §2.2 + §12 methodology MUST be reachable
            in compact contexts; this is the load-bearing 100-minus-rate
            STIR-quote caveat in a single line.  Use the wire's verbatim
            methodology_disclosure as the title= tooltip so YAML edits flow
            to the Monitor tile too. */}
        <div
          className="font-mono text-[9.5px] leading-snug text-fg-faint"
          title={data.methodology_disclosure}
        >
          Back − front, implied-rate bps. STIR quote 100-minus-rate.
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="policy_futures_get_futures_calendar_spread_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn on parent re-renders.

function useFetchDetailPolicyFuturesCalendar(
  params: FuturesCalendarSpreadDetailParams | null,
) {
  const [state, setState] = useState<{
    data: FuturesCalendarSpreadOutput | null;
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
    fetchDetailPolicyFuturesCalendar(params)
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
