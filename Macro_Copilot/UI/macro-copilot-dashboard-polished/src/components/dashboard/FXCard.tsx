import { ArrowUpRight, MoreHorizontal, Waypoints } from 'lucide-react';
import type { FXCardData } from '@/types/dashboard';
import { TerminalCard } from '@/components/ui/TerminalCard';
import { Sparkline } from '@/components/ui/Sparkline';
import { cn } from '@/utils/cn';

type FXCardProps = {
  data: FXCardData;
};

export function FXCard({ data }: FXCardProps) {
  return (
    <TerminalCard
      kicker="FX Agent"
      title={data.title}
      icon={<Waypoints size={12} />}
      action={
        <>
          <span className="chip chip-accent">Live</span>
          <button className="rounded-md p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
            <MoreHorizontal size={14} />
          </button>
        </>
      }
      className="h-full"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        {/* Pair grid */}
        <div className="grid grid-cols-2 gap-3">
          {data.pairs.map((pair) => {
            const isUp = pair.direction === 'up';
            return (
              <div key={pair.symbol} className="subsurface flex flex-col gap-2 p-3.5">
                <div className="flex items-center justify-between">
                  <span className="mono text-[11px] font-semibold tracking-[0.02em] text-fg-primary">
                    {pair.symbol}
                  </span>
                  <span className={cn('chip mono', isUp ? 'chip-pos' : 'chip-neg')}>
                    {pair.delta}
                  </span>
                </div>
                <div className="metric text-[24px] leading-none tracking-[-0.01em]">
                  {pair.value}
                </div>
                <div className="mt-auto text-[10.5px] text-fg-muted">
                  Session · <span className="mono text-fg-secondary">1.0812 / 1.0889</span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Bias chart */}
        <div className="subsurface p-3.5">
          <div className="flex items-center justify-between">
            <div className="kicker">Trading Bias · 5D</div>
            <span className="mono text-[10.5px] text-mint-400">Long USD</span>
          </div>
          <div className="-mx-1 mt-1">
            <Sparkline data={data.series} tone="green" height={56} strokeWidth={1.4} />
          </div>
        </div>

        <div className="mt-auto flex items-center justify-between gap-2 pt-1">
          <div className="text-[11px] text-fg-muted">
            DXY <span className="mono text-fg-secondary">104.86</span>
            <span className="mx-2 text-fg-faint">·</span>
            CARRY-G10 <span className="mono text-mint-400">+1.2σ</span>
          </div>
          <button className="btn-ghost h-8 text-[11.5px]">
            Launch FX
            <ArrowUpRight size={12} />
          </button>
        </div>
      </div>
    </TerminalCard>
  );
}
