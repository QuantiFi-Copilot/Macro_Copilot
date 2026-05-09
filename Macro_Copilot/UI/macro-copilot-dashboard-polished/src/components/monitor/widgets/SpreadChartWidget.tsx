// ============================================================================
// SpreadChartWidget — custom curve-spread chart (e.g. UST 2s10s)
// ----------------------------------------------------------------------------
// Parameterized.  Fetches /api/v1/rates/detail/spread with user-supplied
// (curve_family, short_tenor, long_tenor, lookback_days), renders a
// time-series area chart of the spread plus a current/Δ1d/z-score
// summary on the header.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailSpread,
  type SpreadDetailParams,
} from '@/services/ratesApi';
import type { CurveSpreadOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { Sparkline } from '@/components/ui/Sparkline';
import { WidgetLoading, WidgetError } from './shared';
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

export function SpreadChartWidget({ params }: Props) {
  const fetchParams = useMemo<SpreadDetailParams | null>(() => {
    const cf = typeof params.curve_family === 'string' ? params.curve_family : null;
    const st =
      typeof params.short_tenor === 'string' ? params.short_tenor : undefined;
    const lt =
      typeof params.long_tenor === 'string' ? params.long_tenor : undefined;
    if (!cf) return null;
    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return {
      curve_family: cf,
      short_tenor: st,
      long_tenor: lt,
      lookback_days: lookback,
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailSpread(fetchParams);

  if (!fetchParams) return <WidgetError message="Widget params incomplete." />;
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const dailyNeg = (m.daily_change_bps ?? 0) < 0;
  const zAbs = Math.abs(m.current_z_score ?? 0);
  const zToneClass =
    zAbs >= 2.0
      ? (m.current_z_score ?? 0) > 0
        ? 'text-coral-300'
        : 'text-mint-300'
      : zAbs >= 1.5
        ? 'text-amber-300'
        : 'text-fg-secondary';

  const sparkData = data.time_series.map((row) => ({
    date: row.date,
    value: row.spread_bps,
  }));

  const curveShort = CURVE_LABEL[m.curve_family] ?? m.curve_family;

  return (
    <>
      <WidgetHeader
        kicker={`CURVE SPREAD · ${curveShort.toUpperCase()}`}
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
            short {m.short_tenor_yield?.toFixed(3) ?? '—'} · long{' '}
            {m.long_tenor_yield?.toFixed(3) ?? '—'}
          </span>
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_curve_spread_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------

function useFetchDetailSpread(params: SpreadDetailParams | null) {
  const [state, setState] = useState<{
    data: CurveSpreadOutput | null;
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
    fetchDetailSpread(params)
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
