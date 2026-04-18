import { CalendarClock, MoreHorizontal } from 'lucide-react';
import type { EventMonitorRow } from '@/types/dashboard';
import { TerminalCard } from '@/components/ui/TerminalCard';
import { Sparkline } from '@/components/ui/Sparkline';
import { cn } from '@/utils/cn';

type EventMonitorCardProps = {
  rows: EventMonitorRow[];
};

export function EventMonitorCard({ rows }: EventMonitorCardProps) {
  return (
    <TerminalCard
      kicker="Policy / Events"
      title="Event Monitor"
      icon={<CalendarClock size={12} />}
      action={
        <>
          <span className="chip mono">{rows.length} Upcoming</span>
          <button className="rounded-md p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
            <MoreHorizontal size={14} />
          </button>
        </>
      }
      className="h-full"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="grid grid-cols-[1fr_auto_58px] items-center gap-3 px-1 pb-1">
          <span className="kicker">Event</span>
          <span className="kicker text-right">Implied</span>
          <span className="kicker text-right">Move</span>
        </div>

        <div className="flex flex-1 flex-col gap-1.5">
          {rows.map((row) => {
            const tone = row.tone === 'green' ? 'green' : row.tone === 'blue' ? 'blue' : 'amber';
            const colorClass =
              row.tone === 'green'
                ? 'text-mint-400'
                : row.tone === 'blue'
                  ? 'text-ice-300'
                  : 'text-amber-400';

            return (
              <div
                key={row.event}
                className="group grid grid-cols-[1fr_auto_58px] items-center gap-3 rounded-lg border border-line-subtle bg-white/[0.008] px-3 py-2.5 transition-colors duration-150 ease-sleek hover:border-line-strong hover:bg-white/[0.02]"
              >
                <div className="min-w-0">
                  <div className="truncate text-[12px] font-medium text-fg-primary">
                    {row.event}
                  </div>
                  <div className="mt-0.5 truncate text-[10.5px] text-fg-muted">
                    {row.dateLabel}
                  </div>
                </div>
                <div className="text-right">
                  <div className={cn('mono text-[12px] font-semibold', colorClass)}>
                    {row.probability}
                  </div>
                  <div className="mono text-[10px] text-fg-muted">{row.count} analogs</div>
                </div>
                <div className="h-8 w-[58px]">
                  <Sparkline data={row.series} tone={tone} mode="line" height={32} strokeWidth={1.3} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </TerminalCard>
  );
}
