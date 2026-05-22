// ============================================================================
// SidebarSearch — search input above the workspace lists.
// ----------------------------------------------------------------------------
// Controlled input; lifts the typed query to the parent sidebar so the
// section lists can filter client-side.  Search semantics are
// case-insensitive substring match against ``ws.name`` (substrate-
// authoritative for display).  When the user clears the input the
// query resets to empty and the parent reverts to the un-filtered
// section lists.
// ============================================================================

import { Search } from 'lucide-react';

type Props = {
  value: string;
  onChange: (next: string) => void;
};

export function SidebarSearch({ value, onChange }: Props) {
  return (
    <div className="relative px-2 pt-2 pb-1">
      <Search
        size={11}
        strokeWidth={2}
        className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-fg-faint"
        aria-hidden
      />
      <label className="sr-only" htmlFor="build-sidebar-search">
        Search workspaces
      </label>
      <input
        id="build-sidebar-search"
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search workspaces…"
        className="w-full rounded-md border border-line-soft bg-white/[0.012] py-1.5 pl-7 pr-2 text-[12px] text-fg-primary placeholder:text-fg-faint focus:border-ice-400/40 focus:outline-none focus:ring-1 focus:ring-ice-400/30"
      />
    </div>
  );
}
