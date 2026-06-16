// ============================================================================
// CrossMarketSpreadWidget — custom cross-sovereign spread chart
// ----------------------------------------------------------------------------
// Parameterized.  Fetches /api/v1/rates/detail/cross-market with user-
// supplied (curve_family_1, curve_family_2, tenor, lookback_days),
// renders a time-series area chart of the spread plus current/Δ1d/
// %ile/z-score summary.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailCrossMarket,
  type CrossMarketDetailParams,
} from '@/services/ratesApi';
import type { CrossMarketSpreadOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { Sparkline } from '@/components/ui/Sparkline';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { cn } from '@/utils/cn';

const CURVE_LABEL: Record<string, string> = {
  UST: 'UST',
  DE_BUND: 'Bund',
  UK_GILT: 'Gilt',
  JGB: 'JGB',
  FR_OAT: 'OAT',
  IT_BTP: 'BTP',
  ES_BONO: 'Bono',
  AU_GOVT: 'AUS',
  CANADA_GOVT: 'CAN',
};

type Props = {
  params: Record<string, unknown>;
};

export function CrossMarketSpreadWidget({ params }: Props) {
  const fetchParams = useMemo<CrossMarketDetailParams | null>(() => {
    const cf1 =
      typeof params.curve_family_1 === 'string' ? params.curve_family_1 : null;
    const cf2 =
      typeof params.curve_family_2 === 'string' ? params.curve_family_2 : null;
    const t = typeof params.tenor === 'string' ? params.tenor : '10Y';
    if (!cf1 || !cf2) return null;
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
      tenor: t,
      lookback_days: lookback,
      as_of_date: typeof params.as_of_date === 'string' ? params.as_of_date : undefined,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailCrossMarket(fetchParams);

  if (!fetchParams) return <WidgetError message="Widget params incomplete." />;
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const dailyNeg = (m.daily_change_bps ?? 0) < 0;
  const zAbs = Math.abs(m.current_z_score ?? 0);
  const zToneClass =
    zAbs >= 2.0
      ? 'text-coral-300'
      : zAbs >= 1.5
        ? 'text-amber-300'
        : 'text-fg-secondary';
  void zToneClass;

  const sparkData = data.time_series.map((row) => ({
    date: row.date,
    value: row.spread_bps,
  }));

  const c1Short = CURVE_LABEL[m.curve_family_1] ?? m.curve_family_1;
  const c2Short = CURVE_LABEL[m.curve_family_2] ?? m.curve_family_2;

  return (
    <>
      <WidgetHeader
        kicker={`CROSS-MARKET · ${c1Short.toUpperCase()}-${c2Short.toUpperCase()} · ${m.tenor.toUpperCase()}`}
        title={m.spread_label}
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
          <span className="font-mono text-[26px] font-light leading-none tracking-[-0.012em] text-fg-primary">
            {m.current_spread_bps.toFixed(1)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">bps</span>
          <span
            className={cn(
              'ml-2 font-mono text-[11px] font-medium',
              dailyNeg ? 'text-mint-300' : 'text-coral-300',
            )}
          >
            Δ1d{' '}
            {m.daily_change_bps !== null
              ? `${m.daily_change_bps > 0 ? '+' : ''}${m.daily_change_bps.toFixed(1)}`
              : '—'}
          </span>
          <span className="ml-1 font-mono text-[10.5px] text-fg-muted">
            · %ile {m.percentile_252d?.toFixed(0) ?? '—'}
          </span>
        </div>

        <div className="-mx-1 mt-2 min-h-[120px] flex-1">
          <Sparkline
            data={sparkData}
            tone="rates"
            mode="area"
            height={140}
            strokeWidth={1.4}
          />
        </div>

        <div className="flex items-center justify-between font-mono text-[9.5px] text-fg-faint">
          <span>
            window {m.rolling_window_days}d · {data.time_series.length} obs
          </span>
          <span>
            {c1Short} {m.curve_family_1_yield?.toFixed(3) ?? '—'} · {c2Short}{' '}
            {m.curve_family_2_yield?.toFixed(3) ?? '—'}
          </span>
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_cross_market_spread_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------

function useFetchDetailCrossMarket(params: CrossMarketDetailParams | null) {
  const [state, setState] = useState<{
    data: CrossMarketSpreadOutput | null;
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
    fetchDetailCrossMarket(params)
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
