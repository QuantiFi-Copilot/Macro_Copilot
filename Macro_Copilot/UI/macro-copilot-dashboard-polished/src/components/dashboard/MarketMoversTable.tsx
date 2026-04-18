import { ArrowDownRight, ArrowUpRight, Flame, MoreHorizontal } from 'lucide-react';
import type { MarketMover } from '@/types/dashboard';
import { TerminalCard } from '@/components/ui/TerminalCard';
import { cn } from '@/utils/cn';

type MarketMoversTableProps = {
  rows: MarketMover[];
};

export function MarketMoversTable({ rows }: MarketMoversTableProps) {
  return (
    <TerminalCard
      kicker="Cross-Asset · Global"
      title="Market Movers Today"
      icon={<Flame size={12} />}
      action={
        <>
          <div className="flex items-center gap-1">
            <button className="chip chip-accent">Z ≥ 1.5</button>
            <button className="chip">24h</button>
            <button className="chip">Custom</button>
          </div>
          <button className="rounded-md p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
            <MoreHorizontal size={14} />
          </button>
        </>
      }
      className="h-full"
      bodyClassName="!p-0"
    >
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Header */}
        <div className="grid grid-cols-[1.1fr_1fr_0.9fr_0.9fr_0.7fr] gap-3 border-b border-line-subtle px-5 py-2.5">
          <span className="kicker">Instrument</span>
          <span className="kicker">Last</span>
          <span className="kicker">Δ 1D</span>
          <span className="kicker">5D Z-Score</span>
          <span className="kicker text-right">Owner</span>
        </div>

        {/* Rows */}
        <div className="flex min-w-0 flex-1 flex-col">
          {rows.map((row, idx) => {
            const positive = row.direction === 'up';
            return (
              <div
                key={`${row.region}-${idx}`}
                className="group grid grid-cols-[1.1fr_1fr_0.9fr_0.9fr_0.7fr] items-center gap-3 border-b border-line-subtle px-5 py-3 transition-colors duration-150 ease-sleek last:border-b-0 hover:bg-white/[0.015]"
              >
                <div className="flex min-w-0 items-center gap-2.5">
                  <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line-soft bg-white/[0.018] text-[9.5px] font-semibold text-fg-secondary">
                    {row.region.split(' ')[0]}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate text-[12px] font-medium text-fg-primary">
                      {row.region}
                    </div>
                    <div className="text-[10px] text-fg-muted">Sovereign</div>
                  </div>
                </div>
                <span className="mono text-[12.5px] font-medium text-fg-primary">
                  {row.instrument}
                </span>
                <div className="flex items-center gap-1.5">
                  {positive ? (
                    <ArrowUpRight size={11} className="text-mint-400" />
                  ) : (
                    <ArrowDownRight size={11} className="text-coral-400" />
                  )}
                  <span
                    className={cn(
                      'mono text-[12px] font-semibold',
                      positive ? 'text-mint-400' : 'text-coral-400',
                    )}
                  >
                    {row.change}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      'mono text-[12px] font-semibold',
                      positive ? 'text-mint-400' : 'text-coral-400',
                    )}
                  >
                    {row.zScore}
                  </span>
                  {/* Z-score magnitude bar */}
                  <div className="relative h-1 flex-1 overflow-hidden rounded-full bg-white/[0.04]">
                    <div
                      className={cn(
                        'absolute inset-y-0 left-0 rounded-full',
                        positive ? 'bg-mint-400/70' : 'bg-coral-400/70',
                      )}
                      style={{ width: '72%' }}
                    />
                  </div>
                </div>
                <span className="ml-auto inline-flex h-5 w-5 items-center justify-center rounded-md border border-line-soft bg-white/[0.018] text-[9px] font-semibold text-fg-secondary">
                  {row.badge}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </TerminalCard>
  );
}
