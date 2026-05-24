// ============================================================================
// FXForwardCurveWidget — FX forward-curve term structure for one pair
// ----------------------------------------------------------------------------
// Parameterized.  Fetches /api/v1/fx/forward-curve with the user-
// supplied pair + lookback_days.  Renders the full G10 forward tenor
// strip (1W, 1M, 3M, 6M, 12M) for the requested pair, with raw
// forward points, spot-unit forward points, outright forward, carry
// (bps + annualised %), and the rolling 252-day z-score / percentile
// on the spot-unit forward points series.
//
// Numerically consistent with FXCarryWidget at any given (pair, tenor):
// the two tools share conventions via fx_agent/forwards/_shared.py on
// the backend.
// ============================================================================

import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { fetchFXForwardCurve } from '@/services/fxApi';
import type { FXForwardCurveResponse } from '@/types/fx';
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
  pair: string;
  lookback_days: number;
};

export function FXForwardCurveWidget({ params }: Props) {
  const fetchParams = useMemo<FetchParams | null>(() => {
    const pair =
      typeof params.pair === 'string' && params.pair ? params.pair : null;
    if (!pair) return null;
    const lookbackRaw = params.lookback_days;
    const lookback =
      typeof lookbackRaw === 'number'
        ? lookbackRaw
        : typeof lookbackRaw === 'string' && lookbackRaw
          ? Number(lookbackRaw)
          : 365;
    return { pair, lookback_days: lookback };
  }, [params]);

  const { data, isLoading, error } = useFXForwardCurve(fetchParams);

  if (!fetchParams) {
    return <WidgetError message="Widget params incomplete: ‘pair’ required." />;
  }
  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) {
    return <WidgetLoading label={`Loading ${fetchParams.pair} curve…`} />;
  }

  const rows = data.rows;
  const pair = data.pair;
  const spotDigits = pair.includes('JPY') ? 2 : 4;

  return (
    <>
      <WidgetHeader
        kicker={`FX FORWARD CURVE · ${pair}`}
        title="Forward Curve"
        meta={
          <span className="inline-flex items-center rounded-full bg-white/[0.025] px-2 py-0.5 font-mono text-[10px] tracking-[0.02em] text-fg-muted ring-1 ring-line-soft">
            as-of {data.as_of_date}
          </span>
        }
      />
      <WidgetBody className="px-5 pb-2">
        {rows.length === 0 ? (
          <WidgetEmpty message={`No forward rows for ${pair}.`} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr>
                  <Th>Tenor</Th>
                  <Th align="right">Spot</Th>
                  <Th align="right">Fwd pts</Th>
                  <Th align="right">Outright</Th>
                  <Th align="right">Carry bps</Th>
                  <Th align="right">Ann. carry</Th>
                  <Th align="right">Z (252d)</Th>
                  <Th align="right">Pctile</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, idx) => {
                  const annPositive = r.carry_annualized_pct > 0;
                  return (
                    <tr
                      key={r.tenor}
                      className={cn(
                        'transition-colors duration-150 hover:bg-white/[0.018]',
                        idx > 0 && 'border-t border-line-subtle/40',
                      )}
                    >
                      <Td strong>{r.tenor}</Td>
                      <Td align="right" mono>
                        {r.spot.toFixed(spotDigits)}
                      </Td>
                      <Td align="right" mono>
                        {r.forward_points.toFixed(2)}
                      </Td>
                      <Td align="right" mono>
                        {r.outright_forward.toFixed(spotDigits)}
                      </Td>
                      <Td align="right" mono tone={annPositive ? 'mint' : 'coral'}>
                        {r.carry_bps_spot >= 0 ? '+' : ''}
                        {r.carry_bps_spot.toFixed(2)}
                      </Td>
                      <Td align="right" mono tone={annPositive ? 'mint' : 'coral'}>
                        {annPositive ? '+' : ''}
                        {r.carry_annualized_pct.toFixed(2)}%
                      </Td>
                      <Td align="right" mono tone={zScoreTone(r.z_score)}>
                        {r.z_score === null
                          ? '—'
                          : `${r.z_score >= 0 ? '+' : ''}${r.z_score.toFixed(2)}`}
                      </Td>
                      <Td align="right" mono>
                        {r.percentile_252d === null
                          ? '—'
                          : `${r.percentile_252d.toFixed(1)}`}
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
        toolName="get_fx_forward_curve"
        asOfDate={data.as_of_date}
      />
    </>
  );
}

// ----------------------------------------------------------------------------

function useFXForwardCurve(params: FetchParams | null) {
  const [state, setState] = useState<{
    data: FXForwardCurveResponse | null;
    error: Error | null;
    isLoading: boolean;
  }>({ data: null, error: null, isLoading: !!params });

  const paramsKey = params ? JSON.stringify(params) : null;

  useEffect(() => {
    let cancelled = false;
    if (!params) {
      setState({ data: null, error: null, isLoading: false });
      return;
    }
    setState({ data: null, error: null, isLoading: true });

    fetchFXForwardCurve(params)
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
