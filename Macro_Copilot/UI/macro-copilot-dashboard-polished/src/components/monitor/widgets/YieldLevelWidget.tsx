// ============================================================================
// YieldLevelWidget — single yield (curve × tenor) with sparkline
// ----------------------------------------------------------------------------
// Parameterized.  Fetches /api/v1/rates/detail/yield with the user-
// supplied curve_family + tenor + lookback_days.  Renders a compact
// summary appropriate for the small / medium widget sizes.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailYield,
  type YieldDetailParams,
} from '@/services/ratesApi';
import type { YieldLevelOutput } from '@/types/rates';
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

export function YieldLevelWidget({ params }: Props) {
  const fetchParams = useMemo<YieldDetailParams | null>(() => {
    const cf = typeof params.curve_family === 'string' ? params.curve_family : null;
    const t = typeof params.tenor === 'string' ? params.tenor : null;
    if (!cf || !t) return null;
    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return { curve_family: cf, tenor: t, lookback_days: lookback };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailYield(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const dailyNeg = (m.daily_change_bps ?? 0) < 0;
  const zAbs = Math.abs(m.z_score ?? 0);
  const zToneClass =
    zAbs >= 2.0
      ? (m.z_score ?? 0) > 0
        ? 'text-coral-300'
        : 'text-mint-300'
      : zAbs >= 1.5
        ? 'text-amber-300'
        : 'text-fg-secondary';

  const curveShort = CURVE_LABEL[m.curve_family] ?? m.curve_family;

  return (
    <>
      <WidgetHeader
        kicker={`${curveShort.toUpperCase()} · ${m.tenor.toUpperCase()}`}
        title="Yield Level"
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
          <span className="font-mono text-[28px] font-light leading-none tracking-[-0.012em] text-fg-primary">
            {m.current_yield_pct.toFixed(3)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">%</span>
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
              {m.daily_change_bps !== null
                ? `${m.daily_change_bps > 0 ? '+' : ''}${m.daily_change_bps.toFixed(1)} bps`
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

        {/* Synthetic sparkline from high/low — the detail endpoint
            doesn't ship the full series for the small widget; we
            render a min/max ribbon as a visual cue.  V2 will switch
            to the full series. */}
        {m.high_252d_pct != null && m.low_252d_pct != null && (
          <div className="mt-1 flex items-center gap-2 font-mono text-[9.5px] text-fg-faint">
            <span>L {m.low_252d_pct.toFixed(2)}</span>
            <div className="relative h-1.5 flex-1 rounded-full bg-white/[0.04] ring-1 ring-line-subtle">
              {(() => {
                const range = m.high_252d_pct - m.low_252d_pct;
                if (range === 0) return null;
                const pct =
                  ((m.current_yield_pct - m.low_252d_pct) / range) * 100;
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
      </WidgetBody>
      <WidgetProvenance
        toolName="get_yield_levels_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// Suppresses unused warning if a small widget renderer doesn't use
// Sparkline today; kept imported because larger sizes will.
void Sparkline;

// ----------------------------------------------------------------------------
// Local fetcher — encapsulates loading / error / cancellation.

function useFetchDetailYield(params: YieldDetailParams | null) {
  const [state, setState] = useState<{
    data: YieldLevelOutput | null;
    error: Error | null;
    isLoading: boolean;
  }>({ data: null, error: null, isLoading: !!params });

  // Stable JSON key prevents object-identity churn from re-firing the
  // fetch when parent re-renders with the same param values.
  const paramsKey = params ? JSON.stringify(params) : null;

  useEffect(() => {
    if (!params) {
      setState({ data: null, error: null, isLoading: false });
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, isLoading: true, error: null }));
    fetchDetailYield(params)
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
    // paramsKey captures the meaningful change surface — params object
    // identity is stringified to a stable key so we don't re-fetch on
    // every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramsKey]);

  return state;
}
