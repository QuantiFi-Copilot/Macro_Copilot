// ============================================================================
// WorkspaceSidebarItem — single row in the workspaces sidebar.
// ----------------------------------------------------------------------------
// Renders one ``WorkspaceListItem`` as a compact list row.  The row
// is a NavLink so the browser handles active-route highlighting via
// the same react-router primitive every other sidebar in the app
// uses.
//
// Visual register matches the Sidebar's existing nav rows: single-
// line label + a small subtext + a tiny "pinned" affordance on the
// right (forward-compat — clicking it toggles a future
// ``ws.is_pinned`` flag once the substrate adds the column).
// ============================================================================

import { NavLink } from 'react-router-dom';
import { Pin } from 'lucide-react';
import { cn } from '@/utils/cn';
import type { WorkspaceListItem } from '@/services/workspaceApi';

type Props = {
  workspace: WorkspaceListItem;
  /** True when the parent list says this row is the actively-pinned
   *  one.  PR A doesn't wire a real "pin" action yet — the icon is
   *  decorative until the substrate ships the column. */
  isPinned?: boolean;
};

export function WorkspaceSidebarItem({ workspace, isPinned = false }: Props) {
  const href = `/workspace/${encodeURIComponent(workspace.slug)}`;
  const title = workspace.name?.trim() || 'Untitled workspace';
  const subtitle = formatRelativeTime(workspace.updated_at);

  return (
    <NavLink
      to={href}
      end
      className={({ isActive }) =>
        cn(
          'group flex items-start gap-2 rounded-md px-2 py-1.5 text-left transition-colors duration-150',
          'hover:bg-white/[0.025]',
          isActive
            ? 'bg-ice-500/10 ring-1 ring-inset ring-ice-400/25'
            : 'bg-transparent',
        )
      }
    >
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12px] font-medium tracking-[-0.005em] text-fg-primary">
          {title}
        </div>
        <div className="mt-0.5 truncate text-[10.5px] text-fg-muted">
          {subtitle}
        </div>
      </div>
      <Pin
        size={11}
        strokeWidth={1.75}
        className={cn(
          'mt-0.5 shrink-0 transition-colors',
          isPinned
            ? 'text-ice-300'
            : 'text-transparent group-hover:text-fg-faint',
        )}
      />
    </NavLink>
  );
}

/** Render an ISO timestamp as a terse relative phrase.  Falls back
 *  to a YYYY-MM-DD date when the elapsed time exceeds a few weeks. */
function formatRelativeTime(isoString: string): string {
  let then: Date;
  try {
    then = new Date(isoString);
    if (Number.isNaN(then.getTime())) return 'unknown';
  } catch {
    return 'unknown';
  }
  const now = new Date();
  const diffMs = now.getTime() - then.getTime();
  const diffMin = Math.round(diffMs / 60_000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin} min ago`;
  const diffHr = Math.round(diffMs / 3_600_000);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDays = Math.round(diffMs / 86_400_000);
  if (diffDays === 1) return 'yesterday';
  if (diffDays < 7) return `${diffDays}d ago`;
  if (diffDays < 30) return `${Math.round(diffDays / 7)}w ago`;
  return then.toISOString().slice(0, 10);
}
