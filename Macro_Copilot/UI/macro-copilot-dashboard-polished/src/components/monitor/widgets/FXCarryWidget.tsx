// ============================================================================
// FXCarryWidget — cross-sectional FX forward-implied carry
// ----------------------------------------------------------------------------
// Pre-aggregated; reads from useFxDataContext.  Renders a wide ranked
// table: pair / spot / forward points / outright / carry in bps and
// annualized %, with a signal chip.  Sized "wide" because the eight
// useful columns don't compress to a medium card without truncation.
// ============================================================================

import type { ReactNode } from 'react';
import { useFxDataContext } from '@/components/monitor/FXDataProvider';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { WidgetLoading, WidgetError, WidgetEmpty } from './shared';
import { cn } from '@/utils/cn';

export function FXCarryWidget() {
  const { data, isLoading, error } = useFxDataContext();

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading label="Loading carry…" />;

  const rows = data.carry.rows;
  const tenor = data.carry.tenor;

  return (
    <>
      <WidgetHeader
        kicker={`FX CARRY · ${tenor.toUpperCase()} FORWARD-IMPLIED`}
        title="Carry Monitor"
        meta={
          <span className="inline-flex items-center rounded-full bg-white/[0.025] px-2 py-0.5 font-mono text-[10px] tracking-[0.02em] text-fg-muted ring-1 ring-line-soft">
            tenor {tenor}
          </span>
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
                  <Th>Pair</Th>
                  <Th align="right">Spot</Th>
                  <Th align="right">Fwd pts</Th>
                  <Th align="right">Outright</Th>
                  <Th align="right">Carry bps</Th>
                  <Th align="right">Ann. carry</Th>
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
                      <Td strong>{r.pair}</Td>
                      <Td align="right" mono>
                        {r.spot.toFixed(spotDigits)}
                      </Td>
                      <Td align="right" mono>
                        {r.forward_points.toFixed(2)}
                      </Td>
                      <Td align="right" mono>
                        {r.outright_forward.toFixed(spotDigits)}
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

type Tone = 'mint' | 'coral' | 'neutral';

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
      )}
    >
      {children}
    </td>
  );
}

function SignalChip({ signal }: { signal: string }) {
  const positive = signal.toUpperCase().includes('POSITIVE');
  const negative = signal.toUpperCase().includes('NEGATIVE');
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
