// ============================================================================
// InstrumentStrip — sub-agent (instrument family) filter
// ----------------------------------------------------------------------------
// "All · 18", "Sovereign Bonds · 12", "OIS · 6" tabs with the same
// fade-from-edges underline as the LibraryTabs strip but smaller.
//
// Sub-agents come from the active agent's manifest.sub_agent_counts;
// we render in declaration order (sovereign first, then OIS) plus an
// "All" option at the front.
// ============================================================================

import { cn } from '@/utils/cn';
import { SUB_AGENT_LABELS } from '@/types/library';

type Props = {
  active: string; // 'all' or a sub_agent id
  /** From manifest.sub_agent_counts. */
  counts: Record<string, number>;
  totalCount: number;
  onSelect: (subAgent: string) => void;
};

export function InstrumentStrip({ active, counts, totalCount, onSelect }: Props) {
  const subs = Object.keys(counts);
  return (
    <div className="flex items-end gap-5 border-b border-line-subtle px-8 pb-1.5 pt-2">
      <Tab
        label="All"
        count={totalCount}
        isActive={active === 'all'}
        onClick={() => onSelect('all')}
      />
      {subs.map((s) => (
        <Tab
          key={s}
          label={SUB_AGENT_LABELS[s] ?? s}
          count={counts[s]}
          isActive={active === s}
          onClick={() => onSelect(s)}
        />
      ))}
    </div>
  );
}

function Tab({
  label,
  count,
  isActive,
  onClick,
}: {
  label: string;
  count: number;
  isActive: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'relative flex items-center gap-1.5 pb-1 text-[12px] tracking-[-0.005em] transition-colors duration-200 ease-sleek',
        isActive
          ? 'font-medium text-fg-primary'
          : 'font-normal text-fg-muted hover:text-fg-secondary',
      )}
    >
      <span>{label}</span>
      <span className="font-mono text-[10px] text-fg-faint">· {count}</span>
      {isActive && (
        <span
          aria-hidden
          className="absolute -bottom-px left-0 right-0 h-px rounded-full bg-gradient-to-r from-transparent via-ice-300 to-transparent shadow-[0_0_8px_rgba(122,162,255,0.45)]"
        />
      )}
    </button>
  );
}
