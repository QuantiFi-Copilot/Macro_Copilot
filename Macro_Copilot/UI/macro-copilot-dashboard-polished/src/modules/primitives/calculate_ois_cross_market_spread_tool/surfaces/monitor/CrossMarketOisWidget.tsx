// ============================================================================
// CrossMarketOisWidget — Monitor bento tile for one same-tenor cross-market
// OIS spread (e.g. USD-EUR 2Y SOFR-ESTR, USD-GBP 5Y SOFR-SONIA, EUR-GBP 10Y
// ESTR-SONIA).
// ----------------------------------------------------------------------------
// Parameterised on (curve_family_1, curve_family_2, tenor, lookback_days).
// Cross-curve invariant — curve_family_1 != curve_family_2 — enforced at
// fetch-param resolution so the widget can't dispatch an invalid pair against
// the schema layer.  Fetches the SAME typed-detail endpoint
// (/api/v1/rates/detail/ois-cross-market-spread) the Build views use — the
// standalone-bridge contract (methodology_exposure.md §5.4).
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// The load-bearing policy-path-divergence caveat surfaces via the kicker pair
// label + the desk-canonical one-liner in the footer.  Mirrors the OIS
// curve_spread / cross-market ZCIS widget shapes.  Shows current spread (bps
// + central-bank premium word), 1d change, z-score badge, AND a 252d range
// strip with a current marker (the OIS cross-market wire DOES carry
// high_252d_bps / low_252d_bps — unlike the leaner OIS curve_spread Output,
// so the range strip ships).
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailOisCrossMarketSpread,
  type OisCrossMarketSpreadDetailParams,
} from '@/services/ratesApi';
import type { OisCrossMarketSpreadOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import {
  OIS_CROSS_MARKET_COMPACT_CAVEAT,
  indexPairShortLabel,
  oisFamilyFor,
  pairShortLabel,
  premiumDirectionCaption,
} from '../crossMarketOisShared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

export function CrossMarketOisWidget({ params }: Props) {
  const fetchParams = useMemo<OisCrossMarketSpreadDetailParams | null>(() => {
    const cf1 =
      typeof params.curve_family_1 === 'string' ? params.curve_family_1 : null;
    const cf2 =
      typeof params.curve_family_2 === 'string' ? params.curve_family_2 : null;
    const tenor = typeof params.tenor === 'string' ? params.tenor : null;
    if (!cf1 || !cf2 || cf1 === cf2 || !tenor) return null;

    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return {
      curve_family_1: cf1,
      curve_family_2: cf2,
      tenor,
      lookback_days: lookback,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailOisCrossMarketSpread(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete or both legs identical." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const spreadBps = m.current_spread_bps;
  const dailyNeg = (m.daily_change_bps ?? 0) < 0;
  const zAbs = Math.abs(m.current_z_score ?? 0);
  const aMeta = oisFamilyFor(m.curve_family_1);
  const bMeta = oisFamilyFor(m.curve_family_2);
  const pairLabel = pairShortLabel(m.curve_family_1, m.curve_family_2);
  const indexPair = indexPairShortLabel(m.curve_family_1, m.curve_family_2);
  const flags = aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : '';
  const premiumCaption = premiumDirectionCaption(
    spreadBps,
    m.curve_family_1,
    m.curve_family_2,
  );

  return (
    <>
      <WidgetHeader
        kicker={`${pairLabel} · ${indexPair} · ${m.tenor.toUpperCase()} ${flags}`.trim()}
        title="Cross-Market OIS"
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
            {premiumCaption}
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
            in compact contexts; this is the load-bearing policy-path-
            divergence caveat in a single line.  The full honesty disclosure
            lives on the Extended view's methodology card. */}
        <div className="font-mono text-[9.5px] leading-snug text-fg-faint">
          {OIS_CROSS_MARKET_COMPACT_CAVEAT}
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_ois_cross_market_spread_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn.
function useFetchDetailOisCrossMarketSpread(
  params: OisCrossMarketSpreadDetailParams | null,
) {
  const [state, setState] = useState<{
    data: OisCrossMarketSpreadOutput | null;
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
    fetchDetailOisCrossMarketSpread(params)
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
