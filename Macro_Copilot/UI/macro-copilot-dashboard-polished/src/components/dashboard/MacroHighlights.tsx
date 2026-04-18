import { MoreHorizontal, Newspaper } from 'lucide-react';
import type { MacroHighlight } from '@/types/dashboard';
import { TerminalCard } from '@/components/ui/TerminalCard';
import { Sparkline } from '@/components/ui/Sparkline';

type MacroHighlightsProps = {
  items: MacroHighlight[];
};

export function MacroHighlights({ items }: MacroHighlightsProps) {
  return (
    <TerminalCard
      kicker="Narrative"
      title="Macro Highlights"
      icon={<Newspaper size={12} />}
      action={
        <>
          <span className="chip">Today</span>
          <button className="rounded-md p-1 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary">
            <MoreHorizontal size={14} />
          </button>
        </>
      }
      className="h-full"
      bodyClassName="!px-0 !pt-0 !pb-0"
    >
      <div className="flex min-w-0 flex-1 flex-col">
        {items.map((item, index) => {
          const tone = item.tone === 'green' ? 'green' : item.tone === 'blue' ? 'blue' : 'neutral';
          const dotColor =
            item.tone === 'green'
              ? 'bg-mint-400'
              : item.tone === 'blue'
                ? 'bg-ice-300'
                : 'bg-fg-secondary';

          return (
            <div
              key={item.title}
              className="group grid grid-cols-[1fr_104px] items-center gap-4 border-b border-line-subtle px-5 py-3.5 transition-colors duration-150 ease-sleek last:border-b-0 hover:bg-white/[0.012]"
              style={index === items.length - 1 ? { paddingBottom: '18px' } : undefined}
            >
              <div className="flex min-w-0 items-start gap-2.5">
                <span
                  className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${dotColor} shadow-[0_0_8px_currentColor] opacity-80`}
                />
                <div className="min-w-0">
                  <h4 className="text-[12.5px] font-semibold tracking-[-0.005em] text-fg-primary">
                    {item.title}
                  </h4>
                  <p className="mt-1 text-[11.5px] leading-[1.55] text-fg-muted">
                    {item.body}
                  </p>
                </div>
              </div>
              <div className="h-[42px]">
                <Sparkline data={item.series} tone={tone} mode="line" height={42} strokeWidth={1.3} />
              </div>
            </div>
          );
        })}
      </div>
    </TerminalCard>
  );
}
