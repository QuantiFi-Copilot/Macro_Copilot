// ============================================================================
// LibraryTabs — top-level Primitives / Workflows / Operators
// ----------------------------------------------------------------------------
// Three text-only tabs, fade-from-edges underline on the active one
// (same active-state pattern as TopNav).  Workflows is wired but in
// V1 still routes to the legacy /workflows page (migration to a
// proper Library-internal Workflows tab is a follow-up PR).
// Operators is dimmed with a "soon" badge.
// ============================================================================

import { Link, useNavigate } from 'react-router-dom';
import { cn } from '@/utils/cn';

export type LibraryTab = 'primitives' | 'workflows' | 'operators';

type Props = {
  active: LibraryTab;
  primitiveCount: number;
  workflowCount: number;
};

export function LibraryTabs({ active, primitiveCount, workflowCount }: Props) {
  const navigate = useNavigate();

  return (
    <div className="flex h-[44px] items-end gap-6 border-b border-line-subtle px-8">
      <Tab
        label="PRIMITIVES"
        count={primitiveCount}
        isActive={active === 'primitives'}
        onClick={() => navigate('/library')}
      />
      <Tab
        label="WORKFLOWS"
        count={workflowCount}
        isActive={active === 'workflows'}
        onClick={() => navigate('/workflows')}
      />
      <Tab
        label="OPERATORS"
        count={null}
        isActive={false}
        disabled
        suffixBadge="soon"
        onClick={() => {/* no-op until operators ship */}}
      />
    </div>
  );
}

function Tab({
  label,
  count,
  isActive,
  onClick,
  disabled,
  suffixBadge,
}: {
  label: string;
  count: number | null;
  isActive: boolean;
  onClick: () => void;
  disabled?: boolean;
  suffixBadge?: string;
}) {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      className={cn(
        'group relative flex h-[44px] items-center gap-2 px-1 pb-1 text-[11px] font-semibold uppercase tracking-[0.16em] transition-colors duration-200 ease-sleek',
        isActive
          ? 'text-fg-primary'
          : disabled
            ? 'cursor-not-allowed text-fg-faint'
            : 'text-fg-muted hover:text-fg-secondary',
      )}
    >
      <span>{label}</span>
      {count !== null && (
        <span className="font-mono text-[10px] font-medium tracking-[0.04em] text-fg-muted">
          · {count}
        </span>
      )}
      {suffixBadge && (
        <span className="rounded-full bg-amber-400/10 px-1.5 py-px font-mono text-[8.5px] font-medium uppercase tracking-[0.14em] text-amber-300 ring-1 ring-amber-400/25">
          {suffixBadge}
        </span>
      )}
      {isActive && (
        <span
          aria-hidden
          className="absolute -bottom-px left-0 right-0 h-[1.5px] rounded-full bg-gradient-to-r from-transparent via-ice-300 to-transparent shadow-[0_0_14px_rgba(122,162,255,0.55)]"
        />
      )}
    </button>
  );
}

// Suppress unused warning when consumers only import the tab type
void Link;
