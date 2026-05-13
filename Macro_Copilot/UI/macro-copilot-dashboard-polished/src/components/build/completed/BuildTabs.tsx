// ============================================================================
// BuildTabs — DAG / Results / Parameters / Notes tab selector.
// ----------------------------------------------------------------------------
// Controlled component: parent owns the active tab.  PR A wires
// content for DAG + Results; Parameters + Notes render placeholder
// stubs documenting that they ship in PR B.  The visual register
// matches the LibraryTabs / WorkspaceHeader strip already in the
// codebase so tab affordances feel consistent across pages.
// ============================================================================

import { cn } from '@/utils/cn';

export type BuildTabId = 'dag' | 'results' | 'parameters' | 'notes';

const TABS: { id: BuildTabId; label: string; soon?: boolean }[] = [
  { id: 'dag', label: 'DAG' },
  { id: 'results', label: 'Results' },
  { id: 'parameters', label: 'Parameters', soon: true },
  { id: 'notes', label: 'Notes', soon: true },
];

type Props = {
  active: BuildTabId;
  onSelect: (id: BuildTabId) => void;
};

export function BuildTabs({ active, onSelect }: Props) {
  return (
    <div className="flex shrink-0 items-center gap-1 border-b border-line-subtle px-6">
      {TABS.map((t) => (
        <TabButton
          key={t.id}
          label={t.label}
          soon={t.soon}
          isActive={active === t.id}
          onClick={() => onSelect(t.id)}
        />
      ))}
    </div>
  );
}

function TabButton({
  label,
  isActive,
  soon,
  onClick,
}: {
  label: string;
  isActive: boolean;
  soon?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'group relative -mb-px flex items-center gap-1.5 px-3 py-2.5 text-[12px] font-medium tracking-[-0.005em] transition-colors duration-150',
        isActive ? 'text-fg-primary' : 'text-fg-muted hover:text-fg-secondary',
      )}
    >
      <span>{label}</span>
      {soon && (
        <span className="rounded-sm border border-line-soft bg-white/[0.02] px-1 py-px text-[8px] uppercase tracking-[0.18em] text-fg-faint">
          soon
        </span>
      )}
      <span
        aria-hidden
        className={cn(
          'absolute inset-x-2 -bottom-px h-px transition-opacity',
          isActive
            ? 'bg-ice-300/70 opacity-100'
            : 'bg-transparent opacity-0',
        )}
      />
    </button>
  );
}
