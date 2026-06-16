// ============================================================================
// InflationSwapForwardWidget — Monitor bento tile for one ZCIS implied
// forward inflation swap rate.
// ----------------------------------------------------------------------------
// Parameterised on (curve_family, forward_pair, lookback_days).  Fetches
// the SAME typed-detail endpoint
// (/api/v1/rates/detail/inflation-swap-forward) the extended + compact
// Build views use — the standalone-bridge contract per
// docs_revamped/03_standards/methodology_exposure.md §5.4 ensures one
// endpoint per tool feeds every surface.
//
// Per docs_revamped/03_standards/rendering_density.md §8 the Monitor
// surface is INHERENTLY COMPACT — no separate compact/extended split like
// Build has.  This widget renders at small/medium widget sizes inside the
// Monitor bento grid.
//
// Mirrors the sibling at
// modules/primitives/calculate_ois_forward_rate_tool/surfaces/monitor/OisForwardRateWidget.tsx
// — same shape + chrome, adapted for ZCIS forward rates (forward_zcis_pct
// field, change_1d_bps / z_score_252d field names, forward-pair label
// kicker, per-curve inflation-index short label).
//
// Wire-honesty (PR10 / P5): the methodology disclosure surfaces as the
// widget tooltip via the wire-sourced ``methodology_label`` field on
// ``current_metrics`` — NOT a hardcoded TS literal.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailInflationSwapForward,
  type InflationSwapForwardDetailParams,
} from '@/services/ratesApi';
import type { InflationSwapForwardOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import {
  forwardPairFor,
  forwardShortLabel,
  zcisFamilyFor,
} from '../inflationSwapForwardShared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

export function InflationSwapForwardWidget({ params }: Props) {
  const fetchParams = useMemo<InflationSwapForwardDetailParams | null>(() => {
    const cf = typeof params.curve_family === 'string' ? params.curve_family : null;
    const forwardPairLabel =
      typeof params.forward_pair === 'string' ? params.forward_pair : null;
    if (!cf || !forwardPairLabel) return null;
    const pair = forwardPairFor(forwardPairLabel);
    if (!pair) return null;
    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'string'
        ? Number(lookbackRaw)
        : typeof lookbackRaw === 'number'
          ? lookbackRaw
          : 252;
    return {
      curve_family: cf,
      start_tenor: pair.startTenor,
      end_tenor: pair.endTenor,
      lookback_days: lookback,
      as_of_date: typeof params.as_of_date === 'string' ? params.as_of_date : undefined,
    };
  }, [params]);

  const { data, error, isLoading } =
    useFetchDetailInflationSwapForward(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const dailyNeg = (m.change_1d_bps ?? 0) < 0;
  const zAbs = Math.abs(m.z_score_252d ?? 0);
  const zToneClass =
    zAbs >= 2.0
      ? (m.z_score_252d ?? 0) > 0
        ? 'text-coral-300'
        : 'text-mint-300'
      : zAbs >= 1.5
        ? 'text-amber-300'
        : 'text-fg-secondary';

  const meta = zcisFamilyFor(m.curve_family);
  const shortLabel = meta?.indexShort ?? m.curve_family;
  const flag = meta?.flag ?? '';
  const forwardLabel = forwardShortLabel(m);

  return (
    <>
      <WidgetHeader
        kicker={`${shortLabel} · ${forwardLabel.toUpperCase()} ${flag}`.trim()}
        title="ZCIS Forward Rate"
        meta={
          <span
            title={m.methodology_label}
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              zAbs >= 1.5
                ? 'ring-amber-400/30 bg-amber-400/[0.08] text-amber-300'
                : 'ring-line-soft bg-white/[0.025] text-fg-secondary',
              zToneClass,
            )}
          >
            z {m.z_score_252d?.toFixed(2) ?? '—'}
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
          >
            {m.forward_zcis_pct != null
              ? `${m.forward_zcis_pct >= 0 ? '+' : ''}${m.forward_zcis_pct.toFixed(3)}`
              : '—'}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">%</span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px]">
          <span>
            <span className="text-fg-faint">Δ1d </span>
            <span
              className={cn(
                'font-medium',
                // Convention: positive change = forward inflation
                // compensation repriced HIGHER → coral.  Matches the
                // sibling OIS forward_rate widget convention.
                dailyNeg ? 'text-mint-300' : 'text-coral-300',
              )}
            >
              {m.change_1d_bps !== null
                ? `${m.change_1d_bps > 0 ? '+' : ''}${m.change_1d_bps.toFixed(1)} bps`
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

        {/* 252d range strip with current marker — uses BPS-space high /
            low to convert to PERCENT for the marker positioning, since
            the headline displays in PERCENT.  Same visual cue as the
            sovereign / linker / OIS-forward analogs. */}
        {m.high_252d_bps != null
          && m.low_252d_bps != null
          && m.forward_zcis_pct != null && (
          <div className="mt-1 flex items-center gap-2 font-mono text-[9.5px] text-fg-faint">
            <span>L {(m.low_252d_bps / 100).toFixed(2)}</span>
            <div className="relative h-1.5 flex-1 rounded-full bg-white/[0.04] ring-1 ring-line-subtle">
              {(() => {
                const lowPct = m.low_252d_bps / 100;
                const highPct = m.high_252d_bps / 100;
                const range = highPct - lowPct;
                if (range === 0) return null;
                const pct = ((m.forward_zcis_pct - lowPct) / range) * 100;
                return (
                  <span
                    aria-hidden
                    className="absolute top-1/2 h-3 w-[2px] -translate-y-1/2 rounded-full bg-ice-300 shadow-[0_0_6px_rgba(122,162,255,0.6)]"
                    style={{ left: `calc(${Math.min(100, Math.max(0, pct))}% - 1px)` }}
                  />
                );
              })()}
            </div>
            <span>H {(m.high_252d_bps / 100).toFixed(2)}</span>
          </div>
        )}
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_inflation_swap_forward_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — mirrors useFetchDetailOisForwardRate in the sibling
// widget.  Stable JSON key prevents re-fetch churn on parent re-renders.

function useFetchDetailInflationSwapForward(
  params: InflationSwapForwardDetailParams | null,
) {
  const [state, setState] = useState<{
    data: InflationSwapForwardOutput | null;
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
    fetchDetailInflationSwapForward(params)
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
