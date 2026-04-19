import { GitCompare, MoreHorizontal } from 'lucide-react';
import type { CrossMarketResponse } from '@/types/rates';
import { TerminalCard } from '@/components/ui/TerminalCard';
import { Sparkline } from '@/components/ui/Sparkline';
import { cn } from '@/utils/cn';

type CrossMarketCardProps = {
  data: CrossMarketResponse;
};

export function CrossMarketCard({ data }: CrossMarketCardProps) {
  return (
    <TerminalCard
      kicker="Cross-Market RV"
      title="Sovereign Spreads"
      icon={<GitCompare size={12} />}
      action={
        <button className="rounded-md p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
          <MoreHorizontal size={14} />
        </button>
      }
      className="h-full"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        {data.pairs.map((pair) => {
          const dailyNeg = (pair.daily_change_bps ?? 0) < 0;
          const zAbs = Math.abs(pair.z_score ?? 0);
          const zColor =
            zAbs >= 2.0 ? 'text-coral-400'
            : zAbs >= 1.5 ? 'text-amber-400'
            : 'text-fg-secondary';

          return (
            <div
              key={pair.spread_label}
              className="subsurface flex flex-col gap-2.5 p-3.5"
            >
              {/* Header row */}
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-semibold text-fg-primary">
                  {pair.spread_label}
                </span>
                <span className={cn('chip mono', zAbs >= 1.5 ? 'chip-neg' : '')}>
                  z {pair.z_score?.toFixed(2) ?? '—'}
                </span>
              </div>

              {/* Sparkline */}
              <div className="-mx-1 h-10">
                <Sparkline
                  data={pair.sparkline}
                  tone="blue"
                  mode="area"
                  height={40}
                  strokeWidth={1.2}
                />
              </div>

              {/* Metrics row */}
              <div className="flex items-center justify-between">
                <div className="flex items-baseline gap-2">
                  <span className="mono text-[16px] font-semibold leading-none text-fg-primary">
                    {pair.spread_bps?.toFixed(1) ?? '—'}
                  </span>
                  <span className="mono text-[10.5px] text-fg-muted">bps</span>
                </div>
                <div className="flex items-center gap-3 text-[10.5px]">
                  <div>
                    <span className="text-fg-muted">1D </span>
                    <span className={cn('mono font-semibold', dailyNeg ? 'text-mint-400' : 'text-coral-400')}>
                      {pair.daily_change_bps !== null
                        ? `${pair.daily_change_bps > 0 ? '+' : ''}${pair.daily_change_bps.toFixed(1)}`
                        : '—'}
                    </span>
                  </div>
                  <div>
                    <span className="text-fg-muted">1M </span>
                    <span className={cn(
                      'mono font-semibold',
                      (pair.monthly_change_bps ?? 0) < 0 ? 'text-mint-400' : 'text-coral-400',
                    )}>
                      {pair.monthly_change_bps !== null
                        ? `${pair.monthly_change_bps > 0 ? '+' : ''}${pair.monthly_change_bps.toFixed(1)}`
                        : '—'}
                    </span>
                  </div>
                  <div>
                    <span className="text-fg-muted">%ile </span>
                    <span className="mono font-medium text-fg-secondary">
                      {pair.percentile_252d?.toFixed(0) ?? '—'}%
                    </span>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </TerminalCard>
  );
}
