// ============================================================================
// CrossMarketWidget — sovereign cross-market spreads
// ----------------------------------------------------------------------------
// Pre-aggregated.  Renders the default cross-market pairs (BTP-Bund,
// OAT-Bund, UST-Bund) with a sparkline strip per pair, current spread,
// daily change, monthly change, percentile, z-score chip.
// ============================================================================

import { useRatesDataContext } from '@/components/monitor/RatesDataProvider';
import {
  WidgetHeader,
  WidgetBody,
  WidgetProvenance,
} from '@/components/monitor/WidgetCard';
import { Sparkline } from '@/components/ui/Sparkline';
import { WidgetLoading, WidgetError } from './shared';
import { cn } from '@/utils/cn';

export function CrossMarketWidget() {
  const { data, isLoading, error } = useRatesDataContext();

  if (error && !data) return <WidgetError message={error.message} />;
  if (!data || isLoading) return <WidgetLoading label="Loading cross-market…" />;

  const pairs = data.crossMarket.pairs;

  return (
    <>
      <WidgetHeader
        kicker="CROSS-MARKET RV · 10Y"
        title="Sovereign Spreads"
        meta={
          <span className="font-mono text-[10.5px] text-fg-muted">
            {pairs.length} pairs
          </span>
        }
      />
      <WidgetBody className="space-y-2.5 px-5 pb-3">
        {pairs.map((pair) => {
          const dailyNeg = (pair.daily_change_bps ?? 0) < 0;
          const monthlyNeg = (pair.monthly_change_bps ?? 0) < 0;
          const zAbs = Math.abs(pair.z_score ?? 0);
          const zChipClass =
            zAbs >= 2.0
              ? 'text-coral-300 ring-coral-400/30 bg-coral-400/[0.10]'
              : zAbs >= 1.5
                ? 'text-amber-300 ring-amber-400/30 bg-amber-400/[0.10]'
                : 'text-fg-secondary ring-line-soft bg-white/[0.025]';

          return (
            <div
              key={pair.spread_label}
              className="rounded-[10px] bg-white/[0.012] px-3.5 py-2.5 ring-1 ring-line-subtle"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-[12.5px] font-medium tracking-[-0.005em] text-fg-primary">
                  {pair.spread_label}
                </span>
                <span
                  className={cn(
                    'inline-flex items-center rounded-full px-2 py-[1px] font-mono text-[10px] font-medium tracking-[0.02em] ring-1',
                    zChipClass,
                  )}
                >
                  z {pair.z_score?.toFixed(2) ?? '—'}
                </span>
              </div>

              <div className="-mx-1 mt-1.5 h-9">
                <Sparkline
                  data={pair.sparkline}
                  tone="blue"
                  mode="area"
                  height={36}
                  strokeWidth={1.2}
                />
              </div>

              <div className="mt-1.5 flex items-baseline justify-between">
                <div className="flex items-baseline gap-1.5">
                  <span className="font-mono text-[15px] font-medium tracking-[-0.005em] text-fg-primary">
                    {pair.spread_bps?.toFixed(1) ?? '—'}
                  </span>
                  <span className="font-mono text-[10px] text-fg-muted">bps</span>
                </div>
                <div className="flex items-center gap-2.5 font-mono text-[10px] text-fg-faint">
                  <span>
                    1d{' '}
                    <span
                      className={cn(
                        'font-medium',
                        dailyNeg ? 'text-mint-300' : 'text-coral-300',
                      )}
                    >
                      {pair.daily_change_bps !== null
                        ? `${pair.daily_change_bps > 0 ? '+' : ''}${pair.daily_change_bps.toFixed(1)}`
                        : '—'}
                    </span>
                  </span>
                  <span>
                    1m{' '}
                    <span
                      className={cn(
                        'font-medium',
                        monthlyNeg ? 'text-mint-300' : 'text-coral-300',
                      )}
                    >
                      {pair.monthly_change_bps !== null
                        ? `${pair.monthly_change_bps > 0 ? '+' : ''}${pair.monthly_change_bps.toFixed(1)}`
                        : '—'}
                    </span>
                  </span>
                  <span>
                    %ile{' '}
                    <span className="font-medium text-fg-secondary">
                      {pair.percentile_252d?.toFixed(0) ?? '—'}
                    </span>
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </WidgetBody>
      <WidgetProvenance
        toolName="calculate_cross_market_spread_tool"
        asOfDate={pairs[0]?.as_of_date ?? null}
      />
    </>
  );
}
