import { ArrowUpRight, MoreHorizontal, Shuffle } from 'lucide-react';
import type { FXMatrixRow } from '@/types/dashboard';
import { TerminalCard } from '@/components/ui/TerminalCard';

type FXMatrixCardProps = {
  rows: FXMatrixRow[];
};

export function FXMatrixCard({ rows }: FXMatrixCardProps) {
  return (
    <TerminalCard
      kicker="Cross-Market RV"
      title="Relative Value Matrix"
      icon={<Shuffle size={12} />}
      action={
        <>
          <div className="flex items-center gap-1">
            <button className="chip chip-accent">G10 Rates</button>
            <button className="chip">FX Basis</button>
            <button className="chip">Credit</button>
          </div>
          <button className="rounded-md p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
            <MoreHorizontal size={14} />
          </button>
        </>
      }
      className="h-full"
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {rows.map((row) => (
          <div
            key={row.market}
            className="subsurface group relative flex items-center justify-between gap-4 p-4 transition-colors duration-150 ease-sleek hover:border-line-strong"
          >
            <div className="min-w-0">
              <div className="kicker">{row.market}</div>
              <div className="mt-1.5 flex items-baseline gap-2">
                <span className="metric text-[18px] leading-none">{row.value}</span>
                <span className="mono text-[11px] font-semibold text-mint-400">
                  {row.changeBps}
                </span>
              </div>
              <div className="mt-1.5 text-[10.5px] text-fg-muted">
                5D Z <span className="mono text-fg-secondary">{row.zScore}</span>
                <span className="mx-1.5 text-fg-faint">·</span>
                %ile <span className="mono text-fg-secondary">92%</span>
              </div>
            </div>
            <button className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-line-soft bg-white/[0.015] text-fg-muted transition-colors group-hover:border-line-strong group-hover:text-ice-300">
              <ArrowUpRight size={12} />
            </button>
          </div>
        ))}
      </div>
    </TerminalCard>
  );
}
