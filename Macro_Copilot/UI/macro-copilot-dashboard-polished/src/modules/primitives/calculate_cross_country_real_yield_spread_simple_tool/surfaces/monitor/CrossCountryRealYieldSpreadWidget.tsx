// ============================================================================
// CrossCountryRealYieldSpreadWidget — Monitor bento tile for one same-
// tenor cross-country linker real-yield spread (e.g. USD_TIPS 10Y real
// yield minus GBP_LINKER 10Y real yield, USD_TIPS 5Y minus EUR_FR_LINKER
// 5Y, GBP_LINKER 10Y minus CAD_RRB 10Y).
// ----------------------------------------------------------------------------
// Parameterised on (first_curve_family, second_curve_family, tenor,
// lookback_days).  Cross-country invariant — first_curve_family !=
// second_curve_family — enforced at fetch-param resolution so the widget
// can't dispatch an invalid pair against the schema layer.  Fetches the
// SAME typed-detail endpoint (/api/v1/rates/detail/cross-country-real-
// yield-spread) the Build views use — the standalone-bridge contract
// (methodology_exposure.md §5.4).
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// The load-bearing index-family AND market-structure mismatch caveats
// (USD TIPS / GBP linkers / EUR linkers / CAD RRBs reference different
// inflation indices AND carry different linker liquidity / issuance
// structure) surface via the kicker pair label + the
// methodology_label's structured prose (via title= tooltip).
// Spread is in PERCENT (real yields are quoted in PERCENT — NOT bps);
// daily change is in BPS per desk convention.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailCrossCountryRealYieldSpread,
  type CrossCountryRealYieldSpreadDetailParams,
} from '@/services/ratesApi';
import type { CrossCountryRealYieldSpreadSimpleOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import {
  linkerCurveFor,
  pairShortLabel,
  shortIndexCaveat,
} from '../crossCountryRealYieldSpreadSimpleShared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

export function CrossCountryRealYieldSpreadWidget({ params }: Props) {
  const fetchParams = useMemo<CrossCountryRealYieldSpreadDetailParams | null>(
    () => {
      const first =
        typeof params.first_curve_family === 'string'
          ? params.first_curve_family
          : null;
      const second =
        typeof params.second_curve_family === 'string'
          ? params.second_curve_family
          : null;
      const tenor = typeof params.tenor === 'string' ? params.tenor : null;
      if (!first || !second || !tenor) return null;
      if (first === second) return null;

      const lookbackRaw = params.lookback_days;
      const lookback =
        typeof lookbackRaw === 'string'
          ? Number(lookbackRaw)
          : typeof lookbackRaw === 'number'
            ? lookbackRaw
            : 252;
      return {
        first_curve_family: first,
        second_curve_family: second,
        tenor,
        lookback_days: lookback,
        as_of_date: typeof params.as_of_date === 'string' ? params.as_of_date : undefined,
      };
    },
    [params],
  );

  const { data, error, isLoading } =
    useFetchDetailCrossCountryRealYield(fetchParams);

  if (!fetchParams) {
    return (
      <WidgetError message="Widget params incomplete or both legs identical." />
    );
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const spreadPct = m.current_spread_pct ?? 0;
  const dailyNeg = (m.daily_change_bps ?? 0) < 0;
  const zAbs = Math.abs(m.current_z_score ?? 0);
  const aMeta = linkerCurveFor(m.first_curve_family);
  const bMeta = linkerCurveFor(m.second_curve_family);
  const pairLabel = pairShortLabel(m.first_curve_family, m.second_curve_family);
  const flags = aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : '';
  const caveatLine = shortIndexCaveat(
    m.first_curve_family,
    m.second_curve_family,
  );

  return (
    <>
      <WidgetHeader
        kicker={`${pairLabel} · ${m.tenor.toUpperCase()} RY ${flags}`.trim()}
        title="Cross-Country Real Yield"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              zAbs >= 1.5
                ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
            title={m.methodology_label}
          >
            z {m.current_z_score?.toFixed(2) ?? '—'}
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[28px] font-light leading-none tracking-[-0.012em] text-fg-primary">
            {spreadPct >= 0 ? '+' : ''}
            {spreadPct.toFixed(2)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">%</span>
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

        {m.high_252d_pct != null && m.low_252d_pct != null && (
          <div className="mt-1 flex items-center gap-2 font-mono text-[9.5px] text-fg-faint">
            <span>L {m.low_252d_pct.toFixed(2)}</span>
            <div className="relative h-1.5 flex-1 rounded-full bg-white/[0.04] ring-1 ring-line-subtle">
              {(() => {
                const range = m.high_252d_pct - m.low_252d_pct;
                if (range === 0) return null;
                const pct = ((spreadPct - m.low_252d_pct) / range) * 100;
                return (
                  <span
                    aria-hidden
                    className="absolute top-1/2 h-3 w-[2px] -translate-y-1/2 rounded-full bg-ice-300 shadow-[0_0_6px_rgba(122,162,255,0.6)]"
                    style={{ left: `calc(${Math.min(100, Math.max(0, pct))}% - 1px)` }}
                  />
                );
              })()}
            </div>
            <span>H {m.high_252d_pct.toFixed(2)}</span>
          </div>
        )}

        {/* Per rendering_density.md §2.2 + §12 methodology MUST be reachable
            in compact contexts; this is the load-bearing index-family +
            market-structure mismatch caveat in a single line.  Derived
            from the per-leg curve_family identifiers — methodology_label
            is the long-form prose, exposed via the z-badge title=
            tooltip. */}
        <div className="font-mono text-[9.5px] leading-snug text-fg-faint">
          {caveatLine}
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_cross_country_real_yield_spread_simple_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn.
function useFetchDetailCrossCountryRealYield(
  params: CrossCountryRealYieldSpreadDetailParams | null,
) {
  const [state, setState] = useState<{
    data: CrossCountryRealYieldSpreadSimpleOutput | null;
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
    fetchDetailCrossCountryRealYieldSpread(params)
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
