import { ArrowUpRight, Layers, MoreHorizontal, TrendingDown } from 'lucide-react';
import type { RatesCardData } from '@/types/dashboard';
import { TerminalCard } from '@/components/ui/TerminalCard';
import { Sparkline } from '@/components/ui/Sparkline';

type RatesCardProps = {
  data: RatesCardData;
};

export function RatesCard({ data }: RatesCardProps) {
  return (
    <TerminalCard
      kicker="Rates Agent"
      title={data.title}
      icon={<Layers size={12} />}
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
        {/* Hero metric */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="kicker">{data.subtitle}</div>
            <div className="mt-2 flex items-baseline gap-2.5">
              <span className="metric text-[36px] leading-none">{data.spreadValue}</span>
              <span className="chip chip-neg">
                <TrendingDown size={10} />
                −4.2 bps
              </span>
            </div>
            <div className="mt-1.5 text-[11px] text-fg-muted">
              vs. 5D Z <span className="mono text-fg-secondary">−1.82</span>
              <span className="mx-2 text-fg-faint">·</span>
              1Y %ile <span className="mono text-fg-secondary">7%</span>
            </div>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <div className="kicker text-right">1Y Range</div>
            <div className="mono text-[11px] text-fg-secondary">−52 · +14</div>
          </div>
        </div>

        {/* Spread chart */}
        <div className="-mx-1.5 min-w-0">
          <Sparkline data={data.spreadSeries} tone="rates" height={96} strokeWidth={1.6} />
        </div>

        {/* PCA subsurface */}
        <div className="subsurface p-3.5">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <span className="kicker text-fg-muted">{data.pcaLabel}</span>
              <span className="chip chip-pos mono">{data.pcaValue}σ</span>
            </div>
            <span className="mono text-[10.5px] text-fg-muted">US Level Steepeners</span>
          </div>
          <div className="-mx-1 mt-1">
            <Sparkline data={data.pcaSeries} tone="blue" height={56} strokeWidth={1.4} />
          </div>
        </div>

        <div className="mt-auto flex items-center justify-between gap-2 pt-1">
          <div className="flex items-center gap-1.5">
            <span className="chip">2Y</span>
            <span className="chip">5Y</span>
            <span className="chip chip-accent">10Y</span>
            <span className="chip">30Y</span>
          </div>
          <button className="btn-ghost h-8 text-[11.5px]">
            Launch Rates
            <ArrowUpRight size={12} />
          </button>
        </div>
      </div>
    </TerminalCard>
  );
}
