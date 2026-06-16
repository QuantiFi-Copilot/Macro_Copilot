// ============================================================================
// SwapBreakevenBasisWidget — Monitor bento tile for one same-currency
// swap-vs-bond inflation basis (e.g. USD 10Y, EUR 5Y, GBP 10Y).
// ----------------------------------------------------------------------------
// Parameterised on (pair, tenor, lookback_days) where ``pair`` is the
// currency key that uniquely determines the same-currency triplet
// (ZCIS + nominal + linker) under the V1 invariant.  Fetches the SAME
// typed-detail endpoint (/api/v1/rates/detail/swap-breakeven-basis) the
// Build views use — the standalone-bridge contract (methodology_exposure
// .md §5.4).
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// The LOAD-BEARING basis caveat (NOT a clean liquidity-premium read)
// surfaces via the kicker pair label + the wire's structured
// ``index_family_caveat`` line + a tooltip carrying ``methodology_label``.
// Mirrors the CrossMarketZcisWidget shape.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailSwapBreakevenBasis,
  type SwapBreakevenBasisDetailParams,
} from '@/services/ratesApi';
import type { SwapBreakevenBasisSimpleOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import {
  fallbackBasisCaveat,
  pairForKey,
  pairForZcisFamily,
} from '../swapBreakevenBasisShared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

export function SwapBreakevenBasisWidget({ params }: Props) {
  const fetchParams = useMemo<SwapBreakevenBasisDetailParams | null>(() => {
    // ``pair`` is the currency key; fall back to explicit per-leg params
    // for direct deep-links.
    const pairKey =
      (typeof params.pair === 'string' && params.pair) || null;
    const pair = pairKey ? pairForKey(pairKey) : null;

    const zcis =
      pair?.zcisFamily
      ?? (typeof params.zcis_curve_family === 'string' ? params.zcis_curve_family : null);
    const nominal =
      pair?.nominalFamily
      ?? (typeof params.nominal_curve_family === 'string' ? params.nominal_curve_family : null);
    const linker =
      pair?.linkerFamily
      ?? (typeof params.linker_curve_family === 'string' ? params.linker_curve_family : null);
    const t = typeof params.tenor === 'string' ? params.tenor : null;

    if (!zcis || !nominal || !linker || !t || nominal === linker) return null;

    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return {
      zcis_curve_family: zcis,
      nominal_curve_family: nominal,
      linker_curve_family: linker,
      tenor: t,
      lookback_days: lookback,
      as_of_date: typeof params.as_of_date === 'string' ? params.as_of_date : undefined,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailSwapBreakevenBasis(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete or legs collide." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const dailyNeg = (m.change_1d_bps ?? 0) < 0;
  const zAbs = Math.abs(m.z_score_252d ?? 0);
  const pair = pairForZcisFamily(m.zcis_curve_family);
  const flag = pair?.flag ?? '';
  const pairLabel = pair
    ? `${pair.country} · ${pair.zcisIndexShort} ZCIS vs ${pair.nominalShort}/${pair.linkerShort} BE`
    : `${m.zcis_curve_family} vs ${m.nominal_curve_family}/${m.linker_curve_family}`;
  const caveatLine = m.index_family_caveat ?? fallbackBasisCaveat(pair);

  return (
    <>
      <WidgetHeader
        kicker={`${pairLabel} · ${m.tenor.toUpperCase()} ${flag}`.trim()}
        title="Swap-Breakeven Basis"
        meta={
          <span
            // Carry the wire-honesty disclosure verbatim via a tooltip
            // so a desk reader can hover for the full methodology label
            // without leaving the Monitor surface.
            title={m.methodology_label}
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              zAbs >= 1.5
                ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
          >
            z {m.z_score_252d?.toFixed(2) ?? '—'}
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[28px] font-light leading-none tracking-[-0.012em] text-fg-primary">
            {m.basis_bps >= 0 ? '+' : ''}
            {m.basis_bps.toFixed(1)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">bp</span>
          <span className="font-mono text-[10px] text-fg-faint">
            ({m.basis_pct >= 0 ? '+' : ''}
            {m.basis_pct.toFixed(3)}%)
          </span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px]">
          <span>
            <span className="text-fg-faint">Δ1d </span>
            <span
              className={cn('font-medium', dailyNeg ? 'text-mint-300' : 'text-coral-300')}
            >
              {m.change_1d_bps !== null
                ? `${m.change_1d_bps > 0 ? '+' : ''}${m.change_1d_bps.toFixed(1)} bp`
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
                const pct = ((m.basis_bps - m.low_252d_bps) / range) * 100;
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
            in compact contexts; this is the LOAD-BEARING basis caveat in
            a single line.  Sourced from the wire's index_family_caveat —
            NOT a TS literal. */}
        <div className="font-mono text-[9.5px] leading-snug text-fg-faint">
          {caveatLine}
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_swap_breakeven_basis_simple_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn.
function useFetchDetailSwapBreakevenBasis(
  params: SwapBreakevenBasisDetailParams | null,
) {
  const [state, setState] = useState<{
    data: SwapBreakevenBasisSimpleOutput | null;
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
    fetchDetailSwapBreakevenBasis(params)
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
