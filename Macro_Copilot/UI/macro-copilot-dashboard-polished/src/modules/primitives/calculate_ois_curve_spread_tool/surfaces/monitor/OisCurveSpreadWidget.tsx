// ============================================================================
// OisCurveSpreadWidget — Monitor bento tile for one same-curve OIS tenor
// spread (2-point spread on a single OIS par-swap curve family).
// ----------------------------------------------------------------------------
// Parameterised on (curve, pair, lookback_days) where ``curve`` is the OIS
// curve_family (USD_SOFR_OIS / EUR_ESTR_OIS / GBP_SONIA_OIS / JPY_OIS /
// AUD_OIS / CAD_OIS) and ``pair`` is the registered (short, long) tuple
// selected by label (e.g. "2s10s").  Fetches the SAME typed-detail endpoint
// (/api/v1/rates/detail/ois-curve-spread) the Build views use — the
// standalone-bridge contract (methodology_exposure.md §5.4).
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.  The
// risk-neutral-policy-pricing caveat surfaces via the kicker pair label + the
// methodology card on the Extended view.  Mirrors the OisButterflyWidget
// shape, adapted for the 2-point spread Output (no triplet — pair).  Since
// the OIS curve-spread Output is lean (no wire ``percentile_252d`` /
// ``high_252d_bps`` / ``low_252d_bps``), this widget renders only the spread
// + 1d change + z-score badge — no high/low marker strip.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailOisCurveSpread,
  type OisCurveSpreadDetailParams,
} from '@/services/ratesApi';
import type { OisCurveSpreadOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import {
  OIS_CURVE_SPREAD_COMPACT_CAVEAT,
  OIS_CURVE_SPREAD_PAIRS_BY_CURVE,
  oisFamilyFor,
  spreadShortLabel,
} from '../oisCurveSpreadShared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

export function OisCurveSpreadWidget({ params }: Props) {
  const fetchParams = useMemo<OisCurveSpreadDetailParams | null>(() => {
    // ``curve`` is the OIS curve_family; fall back to an explicit
    // curve_family param for direct deep-links.
    const curveFamily =
      (typeof params.curve === 'string' && params.curve)
      || (typeof params.curve_family === 'string' && params.curve_family)
      || null;
    if (!curveFamily) return null;
    const meta = oisFamilyFor(curveFamily);
    if (!meta) return null;

    // The pair selector value is the registered pair label (e.g. "2s10s").
    // Look up the (short, long) tuple from the per-curve registry so the
    // widget can't dispatch an invalid ordering.
    const pairKey = typeof params.pair === 'string' ? params.pair : null;
    const pairs = OIS_CURVE_SPREAD_PAIRS_BY_CURVE[curveFamily] ?? [];
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
      short_tenor: selected.short,
      long_tenor: selected.long,
      lookback_days: lookback,
      as_of_date: typeof params.as_of_date === 'string' ? params.as_of_date : undefined,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailOisCurveSpread(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const spreadBps = m.current_spread_bps ?? 0;
  const dailyNeg = (m.daily_change_bps ?? 0) < 0;
  const zAbs = Math.abs(m.current_z_score ?? 0);
  const meta = oisFamilyFor(m.curve_family);
  const flag = meta?.flag ?? '';
  const pairLabel = spreadShortLabel(
    fetchParams.short_tenor,
    fetchParams.long_tenor,
  );
  const curveLabel = meta
    ? `${meta.indexShort} · ${meta.marketShort}`
    : m.curve_family;

  return (
    <>
      <WidgetHeader
        kicker={`${curveLabel} · ${pairLabel} ${flag}`.trim()}
        title="OIS Curve Spread"
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
          <span className="font-mono text-[10px] text-fg-faint">
            {spreadBps > 0 ? 'steeper' : spreadBps < 0 ? 'inverted' : 'flat'}
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
        </div>

        {/* Per rendering_density.md §2.2 + §12 methodology MUST be reachable
            in compact contexts; this is the load-bearing risk-neutral
            policy-pricing caveat in a single line.  The full honesty
            disclosure lives on the Extended view's methodology card. */}
        <div className="font-mono text-[9.5px] leading-snug text-fg-faint">
          {OIS_CURVE_SPREAD_COMPACT_CAVEAT}
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_ois_curve_spread_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn.
function useFetchDetailOisCurveSpread(
  params: OisCurveSpreadDetailParams | null,
) {
  const [state, setState] = useState<{
    data: OisCurveSpreadOutput | null;
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
    fetchDetailOisCurveSpread(params)
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
