// ============================================================================
// PolicyFuturesButterflyWidget — Monitor bento tile for one same-curve STIR
// simple butterfly (3-point implied-rate curvature on a single
// policy-futures curve_family).
// ----------------------------------------------------------------------------
// Parameterised on (curve_family, triplet, lookback_days) where ``triplet``
// is the registered (wing_short, body, wing_long) tuple selected by label
// (e.g. "1-2-3", "2-3-4", "1-4-8 (Whites/Reds)").  Fetches the SAME
// typed-detail endpoint (/api/v1/rates/detail/policy-futures-butterfly)
// the Build views use — the standalone-bridge contract
// (methodology_exposure.md §5.4) ensures one endpoint per tool feeds every
// surface.
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// Mirrors the sibling InflationSwapButterflyWidget / OisButterflyWidget
// shape adapted for the policy_futures Output (no _bps wire field — the
// wire reports PERCENT POINTS; the widget multiplies by 100 for the bps
// headline display).
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailPolicyFuturesButterfly,
  type FuturesButterflySimpleDetailParams,
} from '@/services/ratesApi';
import type { FuturesButterflySimpleOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';
import {
  BUTTERFLY_TRIPLETS_BY_CURVE,
  butterflyTripletLabel,
  curveMetaFor,
  pctToBps,
} from '../futuresButterflySimpleShared';

type Props = {
  params: Record<string, unknown>;
};

export function PolicyFuturesButterflyWidget({ params }: Props) {
  const fetchParams = useMemo<FuturesButterflySimpleDetailParams | null>(() => {
    const cf =
      (typeof params.curve_family === 'string' && params.curve_family) || null;
    if (!cf) return null;
    const meta = curveMetaFor(cf);
    if (!meta) return null;

    // ``triplet`` is the registered triplet label (e.g. "1-2-3").  Look up
    // the (wing_short, body, wing_long) tuple from the per-curve registry
    // so the widget can't dispatch an invalid ordering.
    const tripletKey =
      typeof params.triplet === 'string' ? params.triplet : null;
    const triplets = BUTTERFLY_TRIPLETS_BY_CURVE[cf] ?? [];
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
      strip_position_wing_short: selected.wingShort,
      strip_position_body: selected.body,
      strip_position_wing_long: selected.wingLong,
      lookback_days: lookback,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailPolicyFuturesButterfly(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const meta = curveMetaFor(m.curve_family);
  const flyBps = pctToBps(m.butterfly_value_pct) ?? 0;
  const dailyBps = pctToBps(m.daily_change_butterfly_value_pct);
  const highBps = pctToBps(m.high_252d_butterfly_value_pct);
  const lowBps = pctToBps(m.low_252d_butterfly_value_pct);
  const dailyNeg = (dailyBps ?? 0) < 0;
  const zAbs = Math.abs(m.z_score_butterfly ?? 0);
  const flag = meta?.flag ?? '';
  const tripletDisplay = butterflyTripletLabel(
    m.curve_family,
    m.strip_position_wing_short,
    m.strip_position_body,
    m.strip_position_wing_long,
  );
  const curveLabel = meta
    ? `${meta.shortLabel} · ${tripletDisplay}`
    : `${m.curve_family} ${tripletDisplay}`;

  return (
    <>
      <WidgetHeader
        kicker={`${curveLabel} ${flag}`.trim()}
        title="STIR Butterfly"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              zAbs >= 1.5
                ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
          >
            z {m.z_score_butterfly?.toFixed(2) ?? '—'}
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[28px] font-light leading-none tracking-[-0.012em] text-fg-primary">
            {flyBps >= 0 ? '+' : ''}
            {flyBps.toFixed(1)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">bps</span>
          <span className="font-mono text-[10px] text-fg-faint">
            {flyBps > 0 ? 'belly cheap' : flyBps < 0 ? 'belly rich' : 'flat'}
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
            in compact contexts; this is the load-bearing 100-minus-rate
            STIR-quote caveat in a single line.  The full P5 / ADR 0013
            disclosure lives on the Extended view's methodology card. */}
        <div className="font-mono text-[9.5px] leading-snug text-fg-faint">
          Implied-rate bps. STIR quote 100-minus-rate.
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="policy_futures_get_futures_butterfly_simple_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn on parent re-renders.

function useFetchDetailPolicyFuturesButterfly(
  params: FuturesButterflySimpleDetailParams | null,
) {
  const [state, setState] = useState<{
    data: FuturesButterflySimpleOutput | null;
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
    fetchDetailPolicyFuturesButterfly(params)
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
