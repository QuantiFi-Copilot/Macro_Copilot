// ============================================================================
// VariantSiblingCard — one sibling card in the VariantStrip.
// ----------------------------------------------------------------------------
// Mirrors the ``.research-card`` chassis used by Monitor's WidgetCard
// and Library's ToolCard so the variant strip reads in the same
// register as the rest of the polished Build surfaces.  Each card
// carries:
//
//   ┌─────────────────────────────────────────┐
//   │ VARIANT · 2h ago                  ●     │  ← kicker + active dot
//   │ <Variant name>                          │
//   │ ─────────────────────────────────────── │
//   │ /<slug>                          ↗      │  ← slug + open arrow
//   │ lineage <hash>                          │
//   └─────────────────────────────────────────┘
//
// Hover lifts the card a hair (inherits from ``.research-card``) and
// brightens the open-arrow affordance — same interaction language as
// every other clickable card in Build.
//
// ``isActive`` flips the card into a mint-tinted "currently viewing"
// state so when the strip includes the active workspace as a "Base"
// pin, it reads distinctly from the comparable variants.
// ============================================================================

import { NavLink } from 'react-router-dom';
import { ArrowUpRight } from 'lucide-react';
import { cn } from '@/utils/cn';
import type { WorkspaceListItem } from '@/services/workspaceApi';

type Props = {
  sibling: WorkspaceListItem;
  isActive?: boolean;
};

export function VariantSiblingCard({ sibling, isActive }: Props) {
  const href = `/workspace/${encodeURIComponent(sibling.slug)}`;
  const title = sibling.name?.trim() || `/${sibling.slug}`;
  const created = formatRelativeTime(sibling.created_at);
  const lineage = sibling.dag_hash?.slice(0, 8) ?? null;
  const railColor = isActive
    ? 'rgba(63, 214, 154, 0.55)'
    : 'rgba(122, 162, 255, 0.45)';

  return (
    <NavLink
      to={href}
      style={{ ['--rail-color' as string]: railColor }}
      className={cn(
        'research-card group relative flex flex-col overflow-hidden px-4 pt-3 pb-3 transition-transform duration-200 ease-sleek hover:-translate-y-px',
        isActive && 'ring-1 ring-mint-400/40',
      )}
    >
      <span aria-hidden className="research-card-rail" />

      <div className="flex items-center justify-between gap-2">
        <span className="kicker text-fg-muted">
          {isActive ? 'Base · viewing' : 'Variant'}
          {created && (
            <span className="ml-1 normal-case tracking-normal text-fg-faint">
              · {created}
            </span>
          )}
        </span>
        {isActive ? (
          <span
            aria-hidden
            className="inline-flex h-1.5 w-1.5 rounded-full bg-mint-400 shadow-[0_0_6px_rgba(63,214,154,0.6)]"
          />
        ) : (
          <ArrowUpRight
            size={12}
            strokeWidth={1.75}
            aria-hidden
            className="text-fg-faint transition-colors group-hover:text-ice-200"
          />
        )}
      </div>

      <h4 className="mt-1.5 truncate text-[12.5px] font-semibold tracking-[-0.008em] text-fg-primary">
        {title}
      </h4>

      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="truncate font-mono text-[10px] text-fg-muted">
          /{sibling.slug}
        </span>
        {lineage && (
          <span className="lineage-chip shrink-0" title={sibling.dag_hash}>
            <span className="opacity-70">lineage</span>
            <span>{lineage}</span>
          </span>
        )}
      </div>
    </NavLink>
  );
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

/** ISO timestamp → "just now" / "5m ago" / "2h ago" / "3d ago" /
 *  ``YYYY-MM-DD`` (>30d).  Returns null when the input isn't a valid
 *  date so the kicker can drop the "·" separator cleanly. */
function formatRelativeTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 30) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  const days = Math.floor(seconds / 86_400);
  if (days <= 30) return `${days}d ago`;
  // ISO date is already YYYY-MM-DDT...; slice to the date portion.
  return iso.slice(0, 10);
}
