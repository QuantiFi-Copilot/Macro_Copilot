// ============================================================================
// VariantSiblingCard — one sibling row in the VariantStrip.
// ============================================================================

import { NavLink } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { cn } from '@/utils/cn';
import type { WorkspaceListItem } from '@/services/workspaceApi';

type Props = {
  sibling: WorkspaceListItem;
  isActive?: boolean;
};

export function VariantSiblingCard({ sibling, isActive }: Props) {
  const href = `/workspace/${encodeURIComponent(sibling.slug)}`;
  const title = sibling.name?.trim() || `/${sibling.slug}`;
  return (
    <NavLink
      to={href}
      className={cn(
        'group flex items-center gap-2 rounded-md border px-3 py-2 transition-colors',
        isActive
          ? 'border-ice-400/40 bg-ice-500/[0.06]'
          : 'border-line-soft bg-white/[0.012] hover:border-ice-400/30 hover:bg-ice-500/[0.04]',
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="truncate text-[11.5px] font-medium tracking-[-0.005em] text-fg-primary">
          {title}
        </div>
        <div className="mt-0.5 truncate font-mono text-[10px] text-fg-muted">
          /{sibling.slug}
        </div>
      </div>
      <ArrowRight
        size={11}
        strokeWidth={1.75}
        className="shrink-0 text-fg-faint transition-colors group-hover:text-ice-300"
      />
    </NavLink>
  );
}
