// ============================================================================
// WorkspacesSidebar — left rail listing persisted workspaces.
// ----------------------------------------------------------------------------
// Three vertical regions:
//
//   ┌────────────────────────────┐
//   │ Search                     │
//   │ ─────────────────────────  │
//   │ Recent           (section) │
//   │ Pinned           (section) │
//   │ Shared with me   (section) │
//   │ All workspaces   (section) │
//   │ ─────────────────────────  │
//   │ Need inspiration? + status │  (footer, sticky)
//   └────────────────────────────┘
//
// PR A wires the "Recent" + "All workspaces" sections against the
// real ``GET /workspace?filter=...&limit=N`` endpoint.  ``Pinned``
// and ``Shared with me`` render as empty placeholders with helpful
// hints — the substrate columns that back them ship in a later PR
// and the section taxonomy is already declared here so the layout
// doesn't shift when those rows arrive.
//
// Client-side search filters the rendered items by name; the fetch
// itself is unfiltered (avoids a server round-trip per keystroke).
// ============================================================================

import { useMemo, useState } from 'react';
import { Clock, History, Pin, Share2 } from 'lucide-react';
import { useWorkspacesList } from '@/hooks/useWorkspacesList';
import type { WorkspaceListItem } from '@/services/workspaceApi';
import { SidebarFooter } from './SidebarFooter';
import { SidebarSearch } from './SidebarSearch';
import { WorkspaceSidebarSection } from './WorkspaceSidebarSection';

const RECENT_LIMIT = 12;
const ALL_LIMIT = 100;

export function WorkspacesSidebar() {
  const recent = useWorkspacesList({ filter: 'recent', limit: RECENT_LIMIT });
  const all = useWorkspacesList({ filter: 'all', limit: ALL_LIMIT });

  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const filterByName = (xs: WorkspaceListItem[]) =>
    q.length === 0
      ? xs
      : xs.filter((w) => (w.name ?? 'untitled').toLowerCase().includes(q));

  const recentFiltered = useMemo(
    () => filterByName(recent.items),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [recent.items, q],
  );
  // Hide rows already shown in "Recent" from the "All workspaces"
  // section so the same workspace doesn't render twice in the
  // sidebar — the recent set is a prefix of all by definition.
  const recentIds = useMemo(
    () => new Set(recent.items.map((w) => w.workspace_id)),
    [recent.items],
  );
  const allFiltered = useMemo(
    () =>
      filterByName(all.items).filter((w) => !recentIds.has(w.workspace_id)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [all.items, recentIds, q],
  );

  return (
    <aside className="flex h-full min-h-0 flex-col border-r border-line-subtle bg-ink-900/40 backdrop-blur-sm">
      <div className="flex shrink-0 flex-col">
        <div className="flex items-baseline justify-between px-3 pt-4">
          <h2 className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-fg-muted">
            Workspaces
          </h2>
        </div>
        <SidebarSearch value={query} onChange={setQuery} />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1">
        <WorkspaceSidebarSection
          label="Recent"
          icon={Clock}
          items={recentFiltered}
          emptyHint={
            recent.isLoading
              ? 'Loading…'
              : 'No recent workspaces — run a workflow to create one.'
          }
        />

        <WorkspaceSidebarSection
          label="Pinned"
          icon={Pin}
          items={[]}
          emptyHint="No pinned workspaces yet."
        />

        <WorkspaceSidebarSection
          label="Shared with me"
          icon={Share2}
          items={[]}
          emptyHint="No shared workspaces yet."
        />

        <WorkspaceSidebarSection
          label="All workspaces"
          icon={History}
          items={allFiltered}
          emptyHint={
            all.isLoading
              ? 'Loading…'
              : recent.items.length > 0
                ? 'All recent workspaces already shown above.'
                : 'No workspaces in the registry.'
          }
        />
      </div>

      <SidebarFooter />
    </aside>
  );
}
