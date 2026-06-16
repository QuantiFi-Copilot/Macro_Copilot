// ============================================================================
// CrossCountryBreakevenSpreadWidget — Monitor bento tile for one same-tenor
// cross-country bond-implied breakeven spread (e.g. UK 10Y BE - US 10Y BE,
// FR 10Y BE - US 10Y BE, CA 10Y BE - US 10Y BE).
// ----------------------------------------------------------------------------
// Parameterised on (country_a_pair, country_b_pair, tenor, lookback_days)
// where each "pair" is the packed nominal/linker tuple
// (e.g. 'UK_GILT/GBP_LINKER').  Cross-country invariant —
// country_a_pair != country_b_pair AND nominal/linker legs distinct —
// enforced at fetch-param resolution so the widget can't dispatch an
// invalid pair against the schema layer.  Fetches the SAME typed-detail
// endpoint (/api/v1/rates/detail/cross-country-breakeven-spread) the Build
// views use — the standalone-bridge contract (methodology_exposure.md §5.4).
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// The load-bearing index-family-mismatch caveat (USD TIPS → CPI-U /
// UK GILT linkers → RPI / EUR linkers → HICPxT / Canadian RRBs → CPI
// reference different inflation indices) surfaces via the kicker pair
// label + the methodology_label's structured prose (via title= tooltip).
// Mirrors the CrossMarketZcisWidget shape.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailCrossCountryBreakevenSpread,
  type CrossCountryBreakevenSpreadDetailParams,
} from '@/services/ratesApi';
import type { CrossCountryBreakevenSpreadSimpleOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import {
  countryPairFor,
  pairShortLabel,
  shortIndexCaveat,
} from '../crossCountryBreakevenSpreadSimpleShared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

function splitPair(packed: unknown): { nominal: string; linker: string } | null {
  if (typeof packed !== 'string' || !packed.includes('/')) return null;
  const [nominal, linker] = packed.split('/');
  if (!nominal || !linker) return null;
  return { nominal, linker };
}

export function CrossCountryBreakevenSpreadWidget({ params }: Props) {
  const fetchParams = useMemo<CrossCountryBreakevenSpreadDetailParams | null>(
    () => {
      const aPair = splitPair(params.country_a_pair);
      const bPair = splitPair(params.country_b_pair);
      const tenor = typeof params.tenor === 'string' ? params.tenor : null;
      if (!aPair || !bPair || !tenor) return null;
      if (
        aPair.nominal === bPair.nominal
        || aPair.linker === bPair.linker
      ) return null;

      const lookbackRaw = params.lookback_days;
      const lookback =
        typeof lookbackRaw === 'string'
          ? Number(lookbackRaw)
          : typeof lookbackRaw === 'number'
            ? lookbackRaw
            : 252;
      return {
        country_a_nominal_pair: aPair.nominal,
        country_a_linker_pair: aPair.linker,
        country_b_nominal_pair: bPair.nominal,
        country_b_linker_pair: bPair.linker,
        tenor,
        lookback_days: lookback,
        as_of_date: typeof params.as_of_date === 'string' ? params.as_of_date : undefined,
      };
    },
    [params],
  );

  const { data, error, isLoading } =
    useFetchDetailCrossCountryBreakeven(fetchParams);

  if (!fetchParams) {
    return (
      <WidgetError message="Widget params incomplete or both legs identical." />
    );
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const spreadBps = m.current_spread_bps ?? 0;
  const dailyNeg = (m.daily_change_bps ?? 0) < 0;
  const zAbs = Math.abs(m.current_z_score ?? 0);
  const aMeta = countryPairFor(m.country_a_nominal_pair, m.country_a_linker_pair);
  const bMeta = countryPairFor(m.country_b_nominal_pair, m.country_b_linker_pair);
  const pairLabel = pairShortLabel(
    m.country_a_nominal_pair,
    m.country_a_linker_pair,
    m.country_b_nominal_pair,
    m.country_b_linker_pair,
  );
  const flags = aMeta && bMeta ? `${aMeta.flag} ${bMeta.flag}` : '';
  const caveatLine = shortIndexCaveat(
    m.country_a_nominal_pair,
    m.country_a_linker_pair,
    m.country_b_nominal_pair,
    m.country_b_linker_pair,
  );

  return (
    <>
      <WidgetHeader
        kicker={`${pairLabel} · ${m.tenor.toUpperCase()} BE ${flags}`.trim()}
        title="Cross-Country BE"
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
            {spreadBps >= 0 ? '+' : ''}
            {spreadBps.toFixed(1)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">bp</span>
          <span className="font-mono text-[10px] text-fg-faint">
            ({spreadBps >= 0 ? '+' : ''}
            {(spreadBps / 100).toFixed(2)}%)
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
            in compact contexts; this is the load-bearing index-family-
            mismatch caveat in a single line.  Derived from the per-leg
            (nominal, linker) pair identifiers — methodology_label is the
            long-form prose, exposed via the z-badge title= tooltip. */}
        <div className="font-mono text-[9.5px] leading-snug text-fg-faint">
          {caveatLine}
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_cross_country_breakeven_spread_simple_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn.
function useFetchDetailCrossCountryBreakeven(
  params: CrossCountryBreakevenSpreadDetailParams | null,
) {
  const [state, setState] = useState<{
    data: CrossCountryBreakevenSpreadSimpleOutput | null;
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
    fetchDetailCrossCountryBreakevenSpread(params)
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
