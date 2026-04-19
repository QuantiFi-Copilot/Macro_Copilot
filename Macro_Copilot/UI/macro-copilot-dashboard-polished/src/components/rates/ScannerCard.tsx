import { AlertTriangle, MoreHorizontal, Zap } from 'lucide-react';
import type { ScannerResponse } from '@/types/rates';
import { TerminalCard } from '@/components/ui/TerminalCard';
import { cn } from '@/utils/cn';

type ScannerCardProps = {
  data: ScannerResponse;
};

const CURVE_SHORT: Record<string, string> = {
  UST: 'UST', DE_BUND: 'Bund', UK_GILT: 'Gilt', JGB: 'JGB',
  FR_OAT: 'OAT', IT_BTP: 'BTP', ES_BONO: 'Bono',
  AU_GOVT: 'AUS', CANADA_GOVT: 'CAN',
};

export function ScannerCard({ data }: ScannerCardProps) {
  const hasResults = data.results.length > 0;

  return (
    <TerminalCard
      kicker="Anomaly Detection"
      title="Z-Score Scanner"
      icon={<Zap size={12} />}
      action={
        <>
          <span className="chip chip-accent mono">
            {data.results.length} flagged
          </span>
          <button className="rounded-md p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
            <MoreHorizontal size={14} />
          </button>
        </>
      }
      className="h-full"
      bodyClassName={hasResults ? '!p-0' : undefined}
    >
      {!hasResults ? (
        <div className="flex flex-1 items-center justify-center py-8">
          <div className="text-center">
            <div className="mx-auto mb-2 flex h-8 w-8 items-center justify-center rounded-full border border-line-subtle bg-white/[0.02]">
              <AlertTriangle size={14} className="text-fg-muted" />
            </div>
            <p className="text-[12px] text-fg-muted">No extremes above threshold</p>
          </div>
        </div>
      ) : (
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Header */}
          <div className="grid grid-cols-[auto_1fr_60px_60px_56px] items-center gap-2 border-b border-line-subtle px-4 py-2">
            <span className="kicker w-5">#</span>
            <span className="kicker">Instrument</span>
            <span className="kicker text-right">Yield</span>
            <span className="kicker text-right">Δ1D</span>
            <span className="kicker text-right">Z</span>
          </div>

          {/* Rows */}
          <div className="flex min-w-0 flex-1 flex-col">
            {data.results.map((row, idx) => {
              const zAbs = Math.abs(row.z_score ?? 0);
              const isHigh = row.signal === 'EXTREME_HIGH';
              const zColor = zAbs >= 2.5 ? 'text-coral-400' : zAbs >= 2.0 ? 'text-amber-400' : 'text-ice-300';
              const barWidth = Math.min(100, (zAbs / 3.5) * 100);

              return (
                <div
                  key={`${row.curve_family}-${row.tenor}`}
                  className={cn(
                    'group relative grid grid-cols-[auto_1fr_60px_60px_56px] items-center gap-2 px-4 py-2.5 transition-colors duration-100 hover:bg-white/[0.015]',
                    idx < data.results.length - 1 && 'border-b border-line-subtle/30',
                  )}
                >
                  {/* Z-score magnitude bar behind the row */}
                  <div
                    className="absolute inset-y-0 left-0 opacity-[0.04]"
                    style={{
                      width: `${barWidth}%`,
                      background: isHigh
                        ? 'linear-gradient(90deg, rgba(248,113,113,0.8), transparent)'
                        : 'linear-gradient(90deg, rgba(63,214,154,0.8), transparent)',
                    }}
                  />

                  <span className="relative mono text-[10px] font-semibold text-fg-faint w-5">
                    {row.rank}
                  </span>
                  <div className="relative flex items-center gap-2 min-w-0">
                    <span className="flex h-5 w-7 items-center justify-center rounded border border-line-soft bg-white/[0.02] text-[8px] font-bold text-fg-secondary">
                      {(CURVE_SHORT[row.curve_family] ?? row.curve_family).slice(0, 3)}
                    </span>
                    <span className="text-[11.5px] font-medium text-fg-primary">
                      {CURVE_SHORT[row.curve_family] ?? row.curve_family} {row.tenor}
                    </span>
                  </div>
                  <span className="relative mono text-[11px] font-medium text-fg-primary text-right">
                    {row.yield_pct?.toFixed(3) ?? '—'}
                  </span>
                  <span className={cn(
                    'relative mono text-[11px] font-semibold text-right',
                    (row.daily_change_bps ?? 0) < 0 ? 'text-mint-400' : 'text-coral-400',
                  )}>
                    {row.daily_change_bps !== null
                      ? `${row.daily_change_bps > 0 ? '+' : ''}${row.daily_change_bps.toFixed(1)}`
                      : '—'}
                  </span>
                  <span className={cn('relative mono text-[11px] font-bold text-right', zColor)}>
                    {row.z_score?.toFixed(2) ?? '—'}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </TerminalCard>
  );
}
