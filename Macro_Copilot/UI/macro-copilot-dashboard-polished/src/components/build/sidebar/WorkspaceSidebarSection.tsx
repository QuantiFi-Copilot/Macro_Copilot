// ============================================================================
// WorkspaceSidebarSection — labelled list of workspaces in the sidebar.
// ----------------------------------------------------------------------------
// Used for "Recent", "Pinned", "Shared with me", and "All workspaces"
// groups in the sidebar.  Renders a section kicker + N items.  Empty
// sections still render their kicker so the layout doesn't jump as
// data arrives — they show a single dim row hinting at what would
// appear once you create a workspace of that type.
// ============================================================================

import type { LucideIcon } from 'lucide-react';
import { WorkspaceSidebarItem } from './WorkspaceSidebarItem';
import type { WorkspaceListItem } from '@/services/workspaceApi';

type Props = {
  label: string;
  icon: LucideIcon;
  items: WorkspaceListItem[];
  /** What to show when the section is empty — a single dim row of
   *  helper text rather than an empty white space. */
  emptyHint?: string;
};

export function WorkspaceSidebarSection({
  label,
  icon: Icon,
  items,
  emptyHint,
}: Props) {
  return (
    <section className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5 px-2 pt-3 pb-1">
        <Icon
          size={11}
          strokeWidth={1.75}
          className="text-fg-faint"
          aria-hidden
        />
        <h3 className="text-[10px] font-semibold uppercase tracking-[0.16em] text-fg-faint">
          {label}
        </h3>
      </div>

      {items.length === 0 ? (
        <EmptySectionRow hint={emptyHint} />
      ) : (
        <ul className="flex flex-col gap-0.5">
          {items.map((item) => (
            <li key={item.workspace_id}>
              <WorkspaceSidebarItem workspace={item} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function EmptySectionRow({ hint }: { hint?: string }) {
  return (
    <div className="px-2 py-1.5 text-[10.5px] italic text-fg-faint">
      {hint ?? 'No workspaces yet.'}
    </div>
  );
}
