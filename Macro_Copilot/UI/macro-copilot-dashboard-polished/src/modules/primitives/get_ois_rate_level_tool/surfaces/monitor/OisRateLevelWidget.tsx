// ============================================================================
// OisRateLevelWidget — Monitor bento tile for one OIS rate level.
// ----------------------------------------------------------------------------
// Parameterised on (curve_family, tenor, lookback_days).  Fetches the SAME
// typed-detail endpoint (/api/v1/rates/detail/ois-rate-level) the extended
// + compact Build views use — the standalone-bridge contract per
// docs_revamped/03_standards/methodology_exposure.md §5.4 ensures one
// endpoint per tool feeds every surface.
//
// Per docs_revamped/03_standards/rendering_density.md §8 the Monitor
// surface is INHERENTLY COMPACT — no separate compact/extended split like
// Build has.  This widget renders at small/medium widget sizes inside the
// Monitor bento grid.
//
// Mirrors the sovereign analog at
// modules/primitives/get_yield_levels_tool/surfaces/monitor/YieldLevelWidget.tsx
// — same shape + chrome, adapted for OIS par-swap rates (current_rate_pct
// field, _ois_rate series suffix, central-bank flag emoji).
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailOisRateLevel,
  type OisRateLevelDetailParams,
} from '@/services/ratesApi';
import type { OisRateLevelOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { oisFamilyFor } from '../oisRateLevelShared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

export function OisRateLevelWidget({ params }: Props) {
  const fetchParams = useMemo<OisRateLevelDetailParams | null>(() => {
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

  const { data, error, isLoading } = useFetchDetailOisRateLevel(fetchParams);

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

  const meta = oisFamilyFor(m.curve_family);
  const shortLabel = meta?.indexShort ?? m.curve_family;
  const flag = meta?.flag ?? '';

  return (
    <>
      <WidgetHeader
        kicker={`${shortLabel.toUpperCase()} · ${m.tenor.toUpperCase()} ${flag}`.trim()}
        title="OIS Rate Level"
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
          >
            {m.current_rate_pct >= 0 ? '+' : ''}
            {m.current_rate_pct.toFixed(4)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">%</span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px]">
          <span>
            <span className="text-fg-faint">Δ1d </span>
            <span
              className={cn(
                'font-medium',
                // Convention: positive change = rates up = tightening
                // implied policy path → coral.  Matches the sovereign
                // yield_levels widget.
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

        {/* 252d range strip with current marker — same visual cue as the
            sovereign / linker analogs. */}
        {m.high_252d_pct != null && m.low_252d_pct != null && (
          <div className="mt-1 flex items-center gap-2 font-mono text-[9.5px] text-fg-faint">
            <span>L {m.low_252d_pct.toFixed(2)}</span>
            <div className="relative h-1.5 flex-1 rounded-full bg-white/[0.04] ring-1 ring-line-subtle">
              {(() => {
                const range = m.high_252d_pct - m.low_252d_pct;
                if (range === 0) return null;
                const pct = ((m.current_rate_pct - m.low_252d_pct) / range) * 100;
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
        toolName="get_ois_rate_level_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — mirrors useFetchDetailRealYield in the linker analog.
// Stable JSON key prevents re-fetch churn on parent re-renders.

function useFetchDetailOisRateLevel(params: OisRateLevelDetailParams | null) {
  const [state, setState] = useState<{
    data: OisRateLevelOutput | null;
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
    fetchDetailOisRateLevel(params)
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
