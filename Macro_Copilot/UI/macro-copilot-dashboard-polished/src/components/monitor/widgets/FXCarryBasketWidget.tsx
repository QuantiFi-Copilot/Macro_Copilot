// ============================================================================
// FXCarryBasketWidget — FX carry basket STRATEGY INDEX (paper backtest)
// ----------------------------------------------------------------------------
// Fetches /api/v1/fx/carry-basket and renders the cumulative excess-return
// equity curve (area sparkline) plus the headline backtest stats
// (annualised return, vol, Sharpe, max drawdown) and the basket
// constituents. Strategy index, NOT an executable backtest — no TC /
// slippage. Backed by get_fx_carry_basket.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { fetchFXCarryBasket } from '@/services/fxApi';
import type { FXCarryBasketResponse } from '@/types/fx';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from './shared';
import { Sparkline } from '@/components/ui/Sparkline';
import { cn } from '@/utils/cn';

type Props = { params: Record<string, unknown> };

type FetchParams = {
  market_scope: string;
  tenor: string;
  top_n: number;
  basket_construction: string;
};

export function FXCarryBasketWidget({ params }: Props) {
  const fetchParams = useMemo<FetchParams>(() => {
    const market_scope =
      typeof params.market_scope === 'string' ? params.market_scope : 'G10';
    const tenor = typeof params.tenor === 'string' ? params.tenor : '1M';
    const top_n =
      typeof params.top_n === 'number'
        ? params.top_n
        : typeof params.top_n === 'string'
          ? Number(params.top_n)
          : 3;
    const basket_construction =
      typeof params.basket_construction === 'string'
        ? params.basket_construction
        : 'long_short_top_n';
    return { market_scope, tenor, top_n, basket_construction };
  }, [params]);

  const { data, isLoading, error } = useFXCarryBasket(fetchParams);

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) {
    return <WidgetLoading label={`Loading ${fetchParams.market_scope} carry basket…`} />;
  }

  const s = data.snapshot;
  const rows = data.cumulative_excess_return_series?.rows ?? [];
  const curve = rows
    .filter((r) => r.value !== null && r.value !== undefined)
    .map((r) => ({ value: r.value as number, label: r.date }));
  const cumPositive = s.current_cumulative_excess_return_pct >= 0;
  const sharpePositive = (s.sharpe_ratio ?? 0) >= 0;

  return (
    <>
      <WidgetHeader
        kicker={`FX CARRY BASKET · ${s.market_scope} ${s.tenor} · ${s.basket_construction === 'long_only_top_n' ? 'LONG-ONLY' : 'LONG/SHORT'}`}
        title="Carry Basket"
        meta={
          <span className="inline-flex items-center rounded-full bg-white/[0.025] px-2 py-0.5 font-mono text-[10px] tracking-[0.02em] text-fg-muted ring-1 ring-line-soft">
            top {s.top_n} · as-of {s.as_of_date}
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span
            className={cn(
              'font-mono text-[26px] font-light leading-none tracking-[-0.012em]',
              cumPositive ? 'text-mint-300' : 'text-coral-300',
            )}
          >
            {cumPositive ? '+' : ''}
            {s.current_cumulative_excess_return_pct.toFixed(2)}%
          </span>
          <span className="font-mono text-[11px] text-fg-muted">cumulative excess</span>
        </div>

        <div className="-mx-1 mt-1 min-h-[110px] flex-1">
          <Sparkline
            data={curve}
            tone={cumPositive ? 'green' : 'coral'}
            mode="area"
            height={128}
            strokeWidth={1.5}
          />
        </div>

        <div className="grid grid-cols-4 gap-2">
          <Stat label="Ann. ret" value={s.annualized_return_pct} suffix="%" signed />
          <Stat label="Ann. vol" value={s.annualized_volatility_pct} suffix="%" />
          <Stat
            label="Sharpe"
            value={s.sharpe_ratio}
            tone={sharpePositive ? 'mint' : 'coral'}
          />
          <Stat label="Max DD" value={s.max_drawdown_pct} suffix="%" tone="coral" />
        </div>

        {data.constituent_pairs?.length ? (
          <div className="mt-1 flex flex-wrap gap-1">
            {data.constituent_pairs.map((p) => (
              <span
                key={p}
                className="rounded-full bg-white/[0.03] px-2 py-0.5 font-mono text-[10px] text-fg-secondary ring-1 ring-line-soft"
              >
                {p}
              </span>
            ))}
          </div>
        ) : null}
      </WidgetBody>
      <WidgetProvenance toolName="get_fx_carry_basket" asOfDate={s.as_of_date} />
    </>
  );
}

// ----------------------------------------------------------------------------

function Stat({
  label,
  value,
  suffix = '',
  signed = false,
  tone = 'neutral',
}: {
  label: string;
  value: number | null | undefined;
  suffix?: string;
  signed?: boolean;
  tone?: 'neutral' | 'mint' | 'coral';
}) {
  const v = value ?? null;
  const toneClass =
    tone === 'mint'
      ? 'text-mint-300'
      : tone === 'coral'
        ? 'text-coral-300'
        : 'text-fg-primary';
  return (
    <div className="rounded-md bg-white/[0.02] px-2 py-1.5 ring-1 ring-line-subtle/50">
      <div className="text-[8.5px] font-semibold uppercase tracking-[0.14em] text-fg-faint">
        {label}
      </div>
      <div className={cn('mt-0.5 font-mono text-[12.5px]', toneClass)}>
        {v === null
          ? '—'
          : `${signed && v >= 0 ? '+' : ''}${v.toFixed(2)}${suffix}`}
      </div>
    </div>
  );
}

function useFXCarryBasket(params: FetchParams) {
  const [state, setState] = useState<{
    data: FXCarryBasketResponse | null;
    error: Error | null;
    isLoading: boolean;
  }>({ data: null, error: null, isLoading: true });

  const key = JSON.stringify(params);

  useEffect(() => {
    let cancelled = false;
    setState({ data: null, error: null, isLoading: true });
    fetchFXCarryBasket(params)
      .then((data) => {
        if (!cancelled) setState({ data, error: null, isLoading: false });
      })
      .catch((err) => {
        if (!cancelled)
          setState({
            data: null,
            error: err instanceof Error ? err : new Error(String(err)),
            isLoading: false,
          });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return state;
}
