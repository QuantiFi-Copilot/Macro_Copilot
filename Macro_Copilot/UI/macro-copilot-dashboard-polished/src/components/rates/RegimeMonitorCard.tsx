import { Activity, MoreHorizontal } from 'lucide-react';
import type { RegimeResponse, RegimeRow } from '@/types/rates';
import { TerminalCard } from '@/components/ui/TerminalCard';
import { cn } from '@/utils/cn';

type RegimeMonitorCardProps = {
  data: RegimeResponse;
};

const CURVE_SHORT: Record<string, string> = {
  UST: 'UST', DE_BUND: 'Bund', UK_GILT: 'Gilt', JGB: 'JGB',
};

const REGIME_COLORS: Record<string, string> = {
  BULL_STEEPENER: 'bg-mint-400/15 text-mint-400 border-mint-400/25',
  BEAR_STEEPENER: 'bg-coral-400/15 text-coral-400 border-coral-400/25',
  BULL_FLATTENER: 'bg-ice-300/15 text-ice-300 border-ice-300/25',
  BEAR_FLATTENER: 'bg-amber-400/15 text-amber-400 border-amber-400/25',
  PARALLEL_SHIFT: 'bg-white/[0.06] text-fg-secondary border-line-soft',
  TWIST: 'bg-purple-400/15 text-purple-400 border-purple-400/25',
};

const REGIME_SHORT: Record<string, string> = {
  BULL_STEEPENER: 'Bull Steep',
  BEAR_STEEPENER: 'Bear Steep',
  BULL_FLATTENER: 'Bull Flat',
  BEAR_FLATTENER: 'Bear Flat',
  PARALLEL_SHIFT: 'Parallel',
  TWIST: 'Twist',
};

function RegimeChip({ tag }: { tag: string }) {
  const colorClass = REGIME_COLORS[tag] ?? 'bg-white/[0.06] text-fg-muted border-line-soft';
  const label = REGIME_SHORT[tag] ?? tag;

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.06em]',
        colorClass,
      )}
    >
      {label}
    </span>
  );
}

export function RegimeMonitorCard({ data }: RegimeMonitorCardProps) {
  // Group by curve_family, show 1d and 5d side by side
  const curveOrder = ['UST', 'DE_BUND', 'UK_GILT', 'JGB'];
  const byKey = new Map<string, RegimeRow>();
  for (const r of data.regimes) {
    byKey.set(`${r.curve_family}:${r.lookback_period}`, r);
  }

  const curves = curveOrder.filter(
    (c) => byKey.has(`${c}:1d`) || byKey.has(`${c}:5d`),
  );

  return (
    <TerminalCard
      kicker="Curve Dynamics"
      title="Regime Monitor"
      icon={<Activity size={12} />}
      action={
        <button className="rounded-md p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
          <MoreHorizontal size={14} />
        </button>
      }
      className="h-full"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
        {/* Column headers */}
        <div className="grid grid-cols-[72px_1fr_1fr] gap-3 px-1">
          <span className="kicker">Curve</span>
          <span className="kicker text-center">Today (1D)</span>
          <span className="kicker text-center">Week (5D)</span>
        </div>

        {curves.map((curve, idx) => {
          const r1d = byKey.get(`${curve}:1d`);
          const r5d = byKey.get(`${curve}:5d`);

          return (
            <div
              key={curve}
              className={cn(
                'group grid grid-cols-[72px_1fr_1fr] items-center gap-3 rounded-lg border border-line-subtle bg-white/[0.008] px-3.5 py-3 transition-colors duration-150 ease-sleek hover:border-line-strong hover:bg-white/[0.02]',
              )}
            >
              {/* Curve label */}
              <div>
                <div className="text-[12px] font-semibold text-fg-primary">
                  {CURVE_SHORT[curve] ?? curve}
                </div>
                <div className="mt-0.5 text-[10px] text-fg-muted">
                  {r1d?.spread_label ?? '2s10s'}
                </div>
              </div>

              {/* 1D regime */}
              <div className="flex flex-col items-center gap-1">
                {r1d ? (
                  <>
                    <RegimeChip tag={r1d.regime_tag} />
                    <div className="flex items-center gap-2 text-[10px] text-fg-muted">
                      <span>
                        <span className="text-fg-faint">2Y </span>
                        <span className={cn(
                          'mono font-semibold',
                          (r1d.front_change_bps ?? 0) < 0 ? 'text-mint-400' : 'text-coral-400',
                        )}>
                          {r1d.front_change_bps !== null
                            ? `${r1d.front_change_bps > 0 ? '+' : ''}${r1d.front_change_bps.toFixed(1)}`
                            : '—'}
                        </span>
                      </span>
                      <span>
                        <span className="text-fg-faint">10Y </span>
                        <span className={cn(
                          'mono font-semibold',
                          (r1d.back_change_bps ?? 0) < 0 ? 'text-mint-400' : 'text-coral-400',
                        )}>
                          {r1d.back_change_bps !== null
                            ? `${r1d.back_change_bps > 0 ? '+' : ''}${r1d.back_change_bps.toFixed(1)}`
                            : '—'}
                        </span>
                      </span>
                    </div>
                  </>
                ) : (
                  <span className="text-[10px] text-fg-faint">—</span>
                )}
              </div>

              {/* 5D regime */}
              <div className="flex flex-col items-center gap-1">
                {r5d ? (
                  <>
                    <RegimeChip tag={r5d.regime_tag} />
                    <div className="flex items-center gap-2 text-[10px] text-fg-muted">
                      <span>
                        <span className="text-fg-faint">2Y </span>
                        <span className={cn(
                          'mono font-semibold',
                          (r5d.front_change_bps ?? 0) < 0 ? 'text-mint-400' : 'text-coral-400',
                        )}>
                          {r5d.front_change_bps !== null
                            ? `${r5d.front_change_bps > 0 ? '+' : ''}${r5d.front_change_bps.toFixed(1)}`
                            : '—'}
                        </span>
                      </span>
                      <span>
                        <span className="text-fg-faint">10Y </span>
                        <span className={cn(
                          'mono font-semibold',
                          (r5d.back_change_bps ?? 0) < 0 ? 'text-mint-400' : 'text-coral-400',
                        )}>
                          {r5d.back_change_bps !== null
                            ? `${r5d.back_change_bps > 0 ? '+' : ''}${r5d.back_change_bps.toFixed(1)}`
                            : '—'}
                        </span>
                      </span>
                    </div>
                  </>
                ) : (
                  <span className="text-[10px] text-fg-faint">—</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </TerminalCard>
  );
}
