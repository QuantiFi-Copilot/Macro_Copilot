// ============================================================================
// FXCarryWidget — parameterized cross-sectional FX carry scanner
// ----------------------------------------------------------------------------
// Was a pre-aggregated widget reading from useFxDataContext at the
// default 1M tenor. Upgraded to a parameterized widget that fetches
// its own data per widget instance with the user-supplied tenor /
// rank_by / top_n / lookback_days. Backed by calculate_fx_carry on
// the backend (extended in Phase A step 6 to a real scanner with
// rolling z-score, percentile, range on the per-pair carry series).
//
// Backward compatibility: when the widget instance has empty params
// (legacy localStorage layouts with the pre-parameterized fx_carry),
// the registry's defaults kick in (tenor=1M, rank_by=carry_signed,
// top_n=6) and the output matches the pre-upgrade behaviour.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { fetchFXCarry } from '@/services/fxApi';
import type { FXCarryResponse } from '@/types/fx';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError, WidgetEmpty } from './shared';
import { cn } from '@/utils/cn';

type Props = {
  params: Record<string, unknown>;
};

type FetchParams = {
  tenor: string;
  rank_by: string;
  top_n: number;
  lookback_days: number;
};

const RANK_LABELS: Record<string, string> = {
  carry_signed: 'Signed carry',
  abs_carry: '|Carry|',
  abs_z_score: '|Z-score|',
};

export function FXCarryWidget({ params }: Props) {
  // Resolve widget params against sensible defaults. The registry
  // also provides these, but defending here keeps the widget safe
  // when an older localStorage layout lacks the param keys.
  const fetchParams = useMemo<FetchParams>(() => {
    const tenor =
      typeof params.tenor === 'string' && params.tenor ? params.tenor : '1M';
    const rankBy =
      typeof params.rank_by === 'string' && params.rank_by
        ? params.rank_by
        : 'carry_signed';
    const topNRaw = params.top_n;
    const topN =
      typeof topNRaw === 'number'
        ? topNRaw
        : typeof topNRaw === 'string' && topNRaw
          ? Number(topNRaw)
          : 6;
    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'number'
        ? lookbackRaw
        : typeof lookbackRaw === 'string' && lookbackRaw
          ? Number(lookbackRaw)
          : 365;
    return {
      tenor,
      rank_by: rankBy,
      top_n: topN,
      lookback_days: lookback,
    };
  }, [params]);

  const { data, isLoading, error } = useFXCarry(fetchParams);

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading label="Loading carry…" />;

  const rows = data.rows;
  const tenor = data.tenor;
  const rankLabel = RANK_LABELS[fetchParams.rank_by] ?? fetchParams.rank_by;

  return (
    <>
      <WidgetHeader
        kicker={`FX CARRY · ${tenor.toUpperCase()} · RANKED BY ${rankLabel.toUpperCase()}`}
        title="Carry Scanner"
        meta={
          <div className="flex items-center gap-1.5">
            <span className="inline-flex items-center rounded-full bg-white/[0.025] px-2 py-0.5 font-mono text-[10px] tracking-[0.02em] text-fg-muted ring-1 ring-line-soft">
              tenor {tenor}
            </span>
            <span className="inline-flex items-center rounded-full bg-white/[0.025] px-2 py-0.5 font-mono text-[10px] tracking-[0.02em] text-fg-muted ring-1 ring-line-soft">
              top {rows.length}
            </span>
          </div>
        }
      />
      <WidgetBody className="px-5 pb-2">
        {rows.length === 0 ? (
          <WidgetEmpty message="No carry rows returned." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr>
                  <Th>#</Th>
                  <Th>Pair</Th>
                  <Th align="right">Spot</Th>
                  <Th align="right">Fwd pts</Th>
                  <Th align="right">Carry bps</Th>
                  <Th align="right">Ann. carry</Th>
                  <Th align="right">Z (252d)</Th>
                  <Th align="right">Pctile</Th>
                  <Th>Signal</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, idx) => {
                  const spotDigits = r.pair.includes('JPY') ? 2 : 4;
                  const annPositive = r.carry_annualized_pct > 0;
                  return (
                    <tr
                      key={r.pair}
                      className={cn(
                        'transition-colors duration-150 hover:bg-white/[0.018]',
                        idx > 0 && 'border-t border-line-subtle/40',
                      )}
                    >
                      <Td mono>{r.rank}</Td>
                      <Td strong>{r.pair}</Td>
                      <Td align="right" mono>
                        {r.spot.toFixed(spotDigits)}
                      </Td>
                      <Td align="right" mono>
                        {r.forward_points.toFixed(2)}
                      </Td>
                      <Td
                        align="right"
                        mono
                        tone={annPositive ? 'mint' : 'coral'}
                      >
                        {r.carry_bps_spot >= 0 ? '+' : ''}
                        {r.carry_bps_spot.toFixed(2)}
                      </Td>
                      <Td
                        align="right"
                        mono
                        tone={annPositive ? 'mint' : 'coral'}
                      >
                        {annPositive ? '+' : ''}
                        {r.carry_annualized_pct.toFixed(2)}%
                      </Td>
                      <Td
                        align="right"
                        mono
                        tone={zScoreTone(r.carry_z_score)}
                      >
                        {r.carry_z_score === null
                          ? '—'
                          : `${r.carry_z_score >= 0 ? '+' : ''}${r.carry_z_score.toFixed(2)}`}
                      </Td>
                      <Td align="right" mono>
                        {r.carry_percentile_252d === null
                          ? '—'
                          : `${r.carry_percentile_252d.toFixed(1)}`}
                      </Td>
                      <Td>
                        <SignalChip signal={r.carry_signal} />
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_fx_carry"
        asOfDate={rows[0]?.spot_date ?? null}
      />
    </>
  );
}

// ----------------------------------------------------------------------------

function useFXCarry(params: FetchParams) {
  const [state, setState] = useState<{
    data: FXCarryResponse | null;
    error: Error | null;
    isLoading: boolean;
  }>({ data: null, error: null, isLoading: true });

  const paramsKey = JSON.stringify(params);

  useEffect(() => {
    let cancelled = false;
    setState({ data: null, error: null, isLoading: true });

    fetchFXCarry(params)
      .then((data) => {
        if (cancelled) return;
        setState({ data, error: null, isLoading: false });
      })
      .catch((err) => {
        if (cancelled) return;
        setState({
          data: null,
          error: err instanceof Error ? err : new Error(String(err)),
          isLoading: false,
        });
      });

    return () => {
      cancelled = true;
    };
    // paramsKey captures the meaningful change surface; the params
    // object identity itself may change every render of the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramsKey]);

  return state;
}

// ----------------------------------------------------------------------------

function Th({
  children,
  align = 'left',
}: {
  children: ReactNode;
  align?: 'left' | 'right';
}) {
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

type Tone = 'mint' | 'coral' | 'amber' | 'ice' | 'neutral';

function Td({
  children,
  strong,
  mono,
  align = 'left',
  tone = 'neutral',
}: {
  children: ReactNode;
  strong?: boolean;
  mono?: boolean;
  align?: 'left' | 'right';
  tone?: Tone;
}) {
  return (
    <td
      className={cn(
        'px-2 py-2 text-[11.5px] tracking-[-0.005em]',
        mono && 'font-mono',
        align === 'right' && 'text-right',
        strong ? 'font-medium text-fg-primary' : 'text-fg-primary',
        tone === 'mint' && 'text-mint-300',
        tone === 'coral' && 'text-coral-300',
        tone === 'amber' && 'text-amber-300',
        tone === 'ice' && 'text-ice-200',
      )}
    >
      {children}
    </td>
  );
}

function zScoreTone(z: number | null): Tone {
  if (z === null) return 'neutral';
  const abs = Math.abs(z);
  if (abs >= 2.0) return z > 0 ? 'coral' : 'mint';
  if (abs >= 1.5) return z > 0 ? 'amber' : 'ice';
  return 'neutral';
}

function SignalChip({ signal }: { signal: string }) {
  const positive = signal.toUpperCase().includes('POSITIVE') || signal === 'High carry';
  const negative = signal.toUpperCase().includes('NEGATIVE') || signal === 'Low carry';
  const tone = positive
    ? 'bg-mint-400/[0.10] text-mint-300 ring-mint-400/25'
    : negative
      ? 'bg-coral-400/[0.10] text-coral-300 ring-coral-400/25'
      : 'bg-white/[0.025] text-fg-muted ring-line-soft';
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium tracking-[0.02em] ring-1',
        tone,
      )}
    >
      {signal}
    </span>
  );
}
