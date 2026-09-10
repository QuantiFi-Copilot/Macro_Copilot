// ============================================================================
// FXVolSmileWidget — FX implied-vol smile (reconstructed 5-point curve)
// ----------------------------------------------------------------------------
// Fetches /api/v1/fx/vol-smile and reconstructs the iconic smile shape
// across delta from the ATM / 25Δ-RR / 25Δ-BF / 10Δ-RR / 10Δ-BF quotes:
//
//   put_Δ  vol = ATM + BF_Δ − RR_Δ/2
//   call_Δ vol = ATM + BF_Δ + RR_Δ/2
//
// Renders the smile curve (10ΔP · 25ΔP · ATM · 25ΔC · 10ΔC) plus the
// raw RR/BF quote table. Backed by get_fx_vol_smile.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { fetchFXVolSmile } from '@/services/fxApi';
import type { FXVolSmileResponse } from '@/types/fx';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError } from './shared';
import { Sparkline } from '@/components/ui/Sparkline';
import { cn } from '@/utils/cn';

type Props = { params: Record<string, unknown> };
type FetchParams = { pair: string; tenor: string; lookback_days: number };

export function FXVolSmileWidget({ params }: Props) {
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

  const { data, isLoading, error } = useFXVolSmile(fetchParams);

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) {
    return <WidgetLoading label={`Loading ${fetchParams.pair} smile…`} />;
  }

  const m = data.current_metrics;
  const atm = m.atm.current_vol_pts;
  const rr25 = m.rr_25.current_vol_pts;
  const bf25 = m.bf_25.current_vol_pts;
  const rr10 = m.rr_10.current_vol_pts;
  const bf10 = m.bf_10.current_vol_pts;

  // Reconstruct put/call wing vols → the smile shape.
  const put10 = atm + bf10 - rr10 / 2;
  const put25 = atm + bf25 - rr25 / 2;
  const call25 = atm + bf25 + rr25 / 2;
  const call10 = atm + bf10 + rr10 / 2;

  const smile = [
    { value: put10, label: '10ΔP' },
    { value: put25, label: '25ΔP' },
    { value: atm, label: 'ATM' },
    { value: call25, label: '25ΔC' },
    { value: call10, label: '10ΔC' },
  ];
  const labels = smile.map((p) => p.label);

  return (
    <>
      <WidgetHeader
        kicker={`FX VOL SMILE · ${m.pair} ${m.tenor}`}
        title="Vol Smile"
        meta={
          <span className="inline-flex items-center rounded-full bg-white/[0.025] px-2 py-0.5 font-mono text-[10px] tracking-[0.02em] text-fg-muted ring-1 ring-line-soft">
            ATM {atm.toFixed(2)} · as-of {m.as_of_date}
          </span>
        }
      />
      <WidgetBody className="px-5 pb-3">
        <div className="-mx-1">
          <Sparkline data={smile} tone="blue" mode="line" height={120} strokeWidth={1.6} />
        </div>
        <div className="mb-3 flex justify-between px-2 font-mono text-[9px] uppercase tracking-[0.12em] text-fg-faint">
          {labels.map((l) => (
            <span key={l}>{l}</span>
          ))}
        </div>

        <table className="w-full text-left">
          <thead>
            <tr>
              <Th>Quote</Th>
              <Th align="right">Vol pts</Th>
              <Th align="right">Z (252d)</Th>
            </tr>
          </thead>
          <tbody>
            <SmileRow label="ATM" value={atm} z={m.atm.z_score} />
            <SmileRow label="25Δ RR" value={rr25} z={m.rr_25.z_score} signed />
            <SmileRow label="25Δ BF" value={bf25} z={m.bf_25.z_score} />
            <SmileRow label="10Δ RR" value={rr10} z={m.rr_10.z_score} signed />
            <SmileRow label="10Δ BF" value={bf10} z={m.bf_10.z_score} />
          </tbody>
        </table>
      </WidgetBody>
      <WidgetProvenance toolName="get_fx_vol_smile" asOfDate={m.as_of_date} />
    </>
  );
}

// ----------------------------------------------------------------------------

function SmileRow({
  label,
  value,
  z,
  signed = false,
}: {
  label: string;
  value: number;
  z: number | null | undefined;
  signed?: boolean;
}) {
  const zv = z ?? null;
  const rrTone = signed ? (value >= 0 ? 'text-mint-300' : 'text-coral-300') : 'text-fg-primary';
  return (
    <tr className="border-t border-line-subtle/40">
      <Td strong>{label}</Td>
      <Td align="right" mono className={rrTone}>
        {signed && value >= 0 ? '+' : ''}
        {value.toFixed(3)}
      </Td>
      <Td align="right" mono className="text-fg-secondary">
        {zv === null ? '—' : `${zv >= 0 ? '+' : ''}${zv.toFixed(2)}`}
      </Td>
    </tr>
  );
}

function Th({ children, align = 'left' }: { children: ReactNode; align?: 'left' | 'right' }) {
  return (
    <th
      className={cn(
        'px-2 py-1.5 text-[9px] font-semibold uppercase tracking-[0.16em] text-fg-faint',
        align === 'right' && 'text-right',
      )}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  strong,
  mono,
  align = 'left',
  className,
}: {
  children: ReactNode;
  strong?: boolean;
  mono?: boolean;
  align?: 'left' | 'right';
  className?: string;
}) {
  return (
    <td
      className={cn(
        'px-2 py-1.5 text-[11.5px]',
        mono && 'font-mono',
        align === 'right' && 'text-right',
        strong ? 'font-medium text-fg-primary' : 'text-fg-primary',
        className,
      )}
    >
      {children}
    </td>
  );
}

function useFXVolSmile(params: FetchParams) {
  const [state, setState] = useState<{
    data: FXVolSmileResponse | null;
    error: Error | null;
    isLoading: boolean;
  }>({ data: null, error: null, isLoading: true });

  const key = JSON.stringify(params);

  useEffect(() => {
    let cancelled = false;
    setState({ data: null, error: null, isLoading: true });
    fetchFXVolSmile(params)
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
