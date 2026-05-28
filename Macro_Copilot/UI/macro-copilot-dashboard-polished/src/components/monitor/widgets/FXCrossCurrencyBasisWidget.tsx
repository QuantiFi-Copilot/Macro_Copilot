// ============================================================================
// FXCrossCurrencyBasisWidget — CIP basis snapshot (FX↔Rates bridge)
// ----------------------------------------------------------------------------
// Fetches /api/v1/fx/cross-currency-basis. Shows the headline basis (bps),
// the FX-implied-vs-OIS decomposition that produces it, rolling z-score,
// 1d/1w/1m changes, and the 252-day range. Sign convention Bloomberg
// BCRX-style: NEGATIVE = USD scarcity. Cross-domain primitive that reads
// the rates_agent OIS substrate. Backed by get_fx_cross_currency_basis.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import { fetchFXCrossCurrencyBasis } from '@/services/fxApi';
import type { FXCrossCurrencyBasisResponse } from '@/types/fx';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from './shared';
import { cn } from '@/utils/cn';

type Props = { params: Record<string, unknown> };
type FetchParams = { pair: string; tenor: string; lookback_days: number };

export function FXCrossCurrencyBasisWidget({ params }: Props) {
  const fetchParams = useMemo<FetchParams>(() => {
    const pair = typeof params.pair === 'string' && params.pair ? params.pair : 'EURUSD';
    const tenor = typeof params.tenor === 'string' ? params.tenor : '1M';
    const lookback_days =
      typeof params.lookback_days === 'number'
        ? params.lookback_days
        : typeof params.lookback_days === 'string'
          ? Number(params.lookback_days)
          : 365;
    return { pair, tenor, lookback_days };
  }, [params]);

  const { data, isLoading, error } = useFXCrossCurrencyBasis(fetchParams);

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) {
    return <WidgetLoading label={`Loading ${fetchParams.pair} basis…`} />;
  }

  const m = data.current_metrics;
  const basis = m.current_basis_bps;
  // BCRX convention: negative = USD scarcity (the common DM state).
  const basisTone = basis < 0 ? 'text-coral-300' : 'text-mint-300';
  const rangePct =
    m.percentile_252d !== null && m.percentile_252d !== undefined
      ? Math.max(0, Math.min(100, m.percentile_252d))
      : null;

  return (
    <>
      <WidgetHeader
        kicker={`FX XCCY BASIS · ${m.pair} ${m.tenor} · ${m.local_ois_curve} vs ${m.usd_ois_curve}`}
        title="Cross-Currency Basis"
        meta={
          <span
            className={cn(
              'inline-flex items-center rounded-full px-2 py-0.5 font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
              m.z_score !== null && Math.abs(m.z_score) >= 2
                ? 'bg-amber-400/10 text-amber-300 ring-amber-400/30'
                : 'bg-white/[0.025] text-fg-secondary ring-line-soft',
            )}
          >
            z {m.z_score !== null && m.z_score !== undefined ? m.z_score.toFixed(2) : '—'}
          </span>
        }
      />
      <WidgetBody className="flex flex-col gap-2.5 px-5 pb-3">
        <div className="flex items-baseline gap-2">
          <span className={cn('font-mono text-[26px] font-light leading-none tracking-[-0.012em]', basisTone)}>
            {basis >= 0 ? '+' : ''}
            {basis.toFixed(1)}
          </span>
          <span className="font-mono text-[11px] text-fg-muted">bps</span>
          <span className="ml-1 font-mono text-[10px] text-fg-faint">
            {basis < 0 ? 'USD scarce (BCRX −)' : 'USD abundant'}
          </span>
        </div>

        {/* The bridge: FX-implied rate diff − OIS rate diff = basis */}
        <div className="rounded-md bg-white/[0.02] px-3 py-2 ring-1 ring-line-subtle/50">
          <div className="mb-1 text-[8.5px] font-semibold uppercase tracking-[0.14em] text-fg-faint">
            Decomposition (local − USD)
          </div>
          <DecompRow label="FX-implied (CIP)" value={m.current_fx_implied_yield_diff_pct} suffix="%" />
          <DecompRow label="OIS observed" value={m.current_ois_diff_pct} suffix="%" />
          <div className="mt-1 flex items-center justify-between border-t border-line-subtle/50 pt-1">
            <span className="font-mono text-[10px] text-fg-secondary">= basis</span>
            <span className={cn('font-mono text-[11px]', basisTone)}>
              {basis >= 0 ? '+' : ''}
              {basis.toFixed(1)} bps
            </span>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2">
          <Chg label="Δ1d" v={m.daily_change_bps} />
          <Chg label="Δ1w" v={m.weekly_change_bps} />
          <Chg label="Δ1m" v={m.monthly_change_bps} />
        </div>

        {rangePct !== null && (
          <div>
            <div className="mb-1 flex items-center justify-between font-mono text-[9.5px] text-fg-faint">
              <span>{m.low_252d_bps?.toFixed(1) ?? '—'}</span>
              <span>252d range · {rangePct.toFixed(0)}%</span>
              <span>{m.high_252d_bps?.toFixed(1) ?? '—'}</span>
            </div>
            <div className="relative h-1.5 rounded-full bg-white/[0.05]">
              <div
                className="absolute top-1/2 h-2.5 w-0.5 -translate-y-1/2 rounded-full bg-ice-200"
                style={{ left: `${rangePct}%` }}
              />
            </div>
          </div>
        )}
      </WidgetBody>
      <WidgetProvenance toolName="get_fx_cross_currency_basis" asOfDate={m.as_of_date} />
    </>
  );
}

// ----------------------------------------------------------------------------

function DecompRow({ label, value, suffix = '' }: { label: string; value: number; suffix?: string }) {
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="font-mono text-[10px] text-fg-muted">{label}</span>
      <span className="font-mono text-[11px] text-fg-primary">
        {value >= 0 ? '+' : ''}
        {value.toFixed(3)}
        {suffix}
      </span>
    </div>
  );
}

function Chg({ label, v }: { label: string; v: number | null | undefined }) {
  const val = v ?? null;
  const tone = val === null ? 'text-fg-faint' : val < 0 ? 'text-coral-300' : 'text-mint-300';
  return (
    <div className="rounded-md bg-white/[0.02] px-2 py-1.5 ring-1 ring-line-subtle/50">
      <div className="text-[8.5px] font-semibold uppercase tracking-[0.14em] text-fg-faint">{label}</div>
      <div className={cn('mt-0.5 font-mono text-[12px]', tone)}>
        {val === null ? '—' : `${val >= 0 ? '+' : ''}${val.toFixed(1)}`}
      </div>
    </div>
  );
}

function useFXCrossCurrencyBasis(params: FetchParams) {
  const [state, setState] = useState<{
    data: FXCrossCurrencyBasisResponse | null;
    error: Error | null;
    isLoading: boolean;
  }>({ data: null, error: null, isLoading: true });

  const key = JSON.stringify(params);

  useEffect(() => {
    let cancelled = false;
    setState({ data: null, error: null, isLoading: true });
    fetchFXCrossCurrencyBasis(params)
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
