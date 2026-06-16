// ============================================================================
// ZScoreCustomWidget — Monitor bento tile for one custom-window z-score.
// ----------------------------------------------------------------------------
// Parameterised on (curve_family, tenor, z_score_window_days) — the window
// IS the widget's reason to exist: the hand-authored ``yield_snapshot``
// grid and the per-instrument ``yield_level`` tile both carry the FIXED
// 252d z-score; this tile lets the desk pin a stretch read at a chosen
// horizon (e.g. 60d tactical) without methodology rework.  Fetches the
// SAME typed-detail endpoint (/api/v1/rates/detail/zscore-custom) the
// Build views use — the standalone-bridge contract
// (methodology_exposure.md §5.4).
//
// Per rendering_density.md §8 the Monitor surface is INHERENTLY COMPACT.
// Mirrors the breakeven BreakevenInflationWidget shape; the regime read
// uses the shared ZScoreRegimeSlider element.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import {
  fetchDetailZscoreCustom,
  type ZscoreCustomDetailParams,
} from '@/services/ratesApi';
import type { ZscoreCustomOutput } from '@/types/rates';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from '@/components/monitor/widgets/shared';
import { ZScoreRegimeSlider, regimeForZScore } from '@/components/shared/build';
import { familyForCurve } from '../zscoreCustomShared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

export function ZScoreCustomWidget({ params }: Props) {
  const fetchParams = useMemo<ZscoreCustomDetailParams | null>(() => {
    const family =
      typeof params.curve_family === 'string' && params.curve_family
        ? params.curve_family
        : null;
    const t = typeof params.tenor === 'string' ? params.tenor : null;
    const windowRaw = params.z_score_window_days;
    const windowDays =
      typeof windowRaw === 'string'
        ? Number(windowRaw)
        : typeof windowRaw === 'number'
          ? windowRaw
          : NaN;
    if (!family || !t || !Number.isFinite(windowDays)) return null;
    return {
      curve_family: family,
      tenor: t,
      z_score_window_days: windowDays,
      as_of_date: typeof params.as_of_date === 'string' ? params.as_of_date : undefined,
      // Display window stays at the backend default (365) — the tile
      // only renders the snapshot, not the series.
    };
  }, [params]);

  const { data, error, isLoading } = useFetchDetailZscoreCustom(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading />;

  const m = data.current_metrics;
  const zAbs = Math.abs(m.current_z_score ?? 0);
  const meta = familyForCurve(m.curve_family);
  const kickerLabel = meta
    ? `${meta.country} · ${meta.curveShort} ${m.tenor.toUpperCase()} ${meta.flag}`
    : `${m.curve_family} ${m.tenor.toUpperCase()}`;

  return (
    <>
      <WidgetHeader
        kicker={kickerLabel.trim()}
        title="Custom-Window Z-Score"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              'ring-line-soft bg-white/[0.025] text-fg-secondary',
            )}
          >
            {m.z_score_window_days_used}d window
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span
            className={cn(
              'font-mono text-[28px] font-light leading-none tracking-[-0.012em]',
              zAbs >= 2.0
                ? (m.current_z_score ?? 0) > 0
                  ? 'text-coral-300'
                  : 'text-mint-300'
                : zAbs >= 1.5
                  ? 'text-amber-300'
                  : 'text-fg-primary',
            )}
          >
            {m.current_z_score != null
              ? `${m.current_z_score >= 0 ? '+' : ''}${m.current_z_score.toFixed(2)}`
              : '—'}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">σ</span>
          <span className="ml-1 font-mono text-[10.5px] text-fg-secondary">
            {regimeForZScore(m.current_z_score)}
          </span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[10.5px]">
          <span>
            <span className="text-fg-faint">yield </span>
            <span className="font-medium text-fg-secondary">
              {m.current_yield_pct != null
                ? `${m.current_yield_pct.toFixed(2)}%`
                : '—'}
            </span>
          </span>
          <span className="text-fg-faint">·</span>
          <span>
            <span className="text-fg-faint">obs </span>
            <span className="font-medium text-fg-secondary">
              {m.observation_count}
            </span>
          </span>
        </div>

        <div className="mt-1">
          <ZScoreRegimeSlider value={m.current_z_score} />
        </div>
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_zscore_custom_tool"
        asOfDate={m.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------
// Local fetcher — stable JSON key prevents re-fetch churn.
function useFetchDetailZscoreCustom(params: ZscoreCustomDetailParams | null) {
  const [state, setState] = useState<{
    data: ZscoreCustomOutput | null;
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
    fetchDetailZscoreCustom(params)
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
