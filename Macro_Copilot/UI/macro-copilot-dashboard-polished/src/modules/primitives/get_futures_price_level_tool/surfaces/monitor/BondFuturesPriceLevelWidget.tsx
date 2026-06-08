// ============================================================================
// BondFuturesPriceLevelWidget — Monitor bento tile for one bond_futures
// rolling-generic contract.
// ----------------------------------------------------------------------------
// Parameterised on (curve_family, contract_code, lookback_days).  Fetches
// the SAME typed-detail endpoint (/api/v1/rates/detail/bond-futures-price)
// the extended + compact Build views use — the standalone-bridge contract
// per methodology_exposure.md §5.4 ensures one endpoint per tool feeds
// every surface.
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT —
// no separate compact/extended split like Build has.  This widget renders
// at small/medium widget sizes inside the Monitor bento grid.
//
// Mirrors the policy_futures sibling tile shape: kicker (contract · tenor),
// title, z-score badge, headline number (PRICE in quote_units; 32nds for
// UST family), 1d change + 252d percentile, and a 252d range strip with
// a current marker on the PRICE axis.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailBondFuturesPrice,
  type BondFuturesPriceDetailParams,
} from '@/services/ratesApi';
import type { BondFuturesPriceLevelOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';
import {
  BOND_FUTURES_PRICE_COMPACT_CAVEAT,
  contractMetaFor,
  formatPrice,
  formatPriceChange,
  quoteUnitsLabel,
} from '../bondFuturesPriceShared';

type Props = {
  params: Record<string, unknown>;
};

export function BondFuturesPriceLevelWidget({ params }: Props) {
  const fetchParams = useMemo<BondFuturesPriceDetailParams | null>(() => {
    const cf = typeof params.curve_family === 'string' ? params.curve_family : null;
    const cc = typeof params.contract_code === 'string' ? params.contract_code : null;
    if (!cf || !cc) return null;
    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return { curve_family: cf, contract_code: cc, lookback_days: lookback };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailBondFuturesPrice(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const meta = contractMetaFor(m.contract_code);
  const wireUnits = quoteUnitsLabel(m.quote_units, meta);
  const dailyDelta = m.daily_change_price;
  // Price up = yield down = rally → mint; price down = yield up → coral.
  const dailyRally = (dailyDelta ?? 0) > 0;
  const z = m.z_score ?? 0;
  const zAbs = Math.abs(z);

  const tenorLabel = meta?.tenor ?? m.tenor ?? '';
  const shortLabel = meta?.shortLabel ?? '';
  const flag = meta?.flag ?? '';

  return (
    <>
      <WidgetHeader
        kicker={`${m.contract_code.toUpperCase()} · ${tenorLabel} ${shortLabel} ${flag}`.trim()}
        title="Bond Futures Price"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              zAbs >= 1.5
                ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
          >
            z {m.z_score?.toFixed(2) ?? '—'}
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span
            className={cn(
              'font-mono text-[28px] font-light leading-none tracking-[-0.012em]',
              'text-fg-primary',
            )}
            title={BOND_FUTURES_PRICE_COMPACT_CAVEAT}
          >
            {formatPrice(m.current_price, meta)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">{wireUnits}</span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px]">
          <span>
            <span className="text-fg-faint">Δ1d </span>
            <span
              className={cn(
                'font-medium',
                dailyRally ? 'text-mint-300' : 'text-coral-300',
              )}
            >
              {dailyDelta != null ? formatPriceChange(dailyDelta, meta) : '—'}
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
            <span className="text-fg-faint">N </span>
            <span className="font-medium text-fg-secondary">
              {m.observation_count}
            </span>
          </span>
        </div>

        {/* 252d price range strip with a current marker. */}
        {m.high_252d_price != null && m.low_252d_price != null && (
          <div className="mt-1 flex items-center gap-2 font-mono text-[9.5px] text-fg-faint">
            <span>L {formatPrice(m.low_252d_price, meta)}</span>
            <div className="relative h-1.5 flex-1 rounded-full bg-white/[0.04] ring-1 ring-line-subtle">
              {(() => {
                const range = m.high_252d_price - m.low_252d_price;
                if (range === 0) return null;
                const pct =
                  ((m.current_price - m.low_252d_price) / range) * 100;
                return (
                  <span
                    aria-hidden
                    className="absolute top-1/2 h-3 w-[2px] -translate-y-1/2 rounded-full bg-ice-300 shadow-[0_0_6px_rgba(122,162,255,0.6)]"
                    style={{ left: `calc(${Math.min(100, Math.max(0, pct))}% - 1px)` }}
                  />
                );
              })()}
            </div>
            <span>H {formatPrice(m.high_252d_price, meta)}</span>
          </div>
        )}
      </WidgetBody>
      <WidgetProvenance
        toolName="get_futures_price_level_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — mirrors the policy_futures analog.  Stable JSON key
// prevents re-fetch churn on parent re-renders.

function useFetchDetailBondFuturesPrice(
  params: BondFuturesPriceDetailParams | null,
) {
  const [state, setState] = useState<{
    data: BondFuturesPriceLevelOutput | null;
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
    fetchDetailBondFuturesPrice(params)
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
