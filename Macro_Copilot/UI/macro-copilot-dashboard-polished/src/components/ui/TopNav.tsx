// ============================================================================
// TopNav — global navigation, refactored for the Ask-led IA
// ----------------------------------------------------------------------------
// Five surfaces: Monitor · Ask · Build · Library · Briefcase.
//
// Active-state mapping (pages we keep working pre-revamp):
//   Monitor    → /, /rates, /fx, /policy, /events, /workflows  (existing
//                Dashboard / Rates / placeholders; revamp ships in
//                later PRs)
//   Ask        → /ask                                          (NEW)
//   Build      → /workspace                                    (existing)
//   Library    → /tools, /workflows                            (existing)
//   Briefcase  → /briefcase                                    (NEW
//                placeholder — proper surface ships in V2)
//
// Drop from previous version:
//   - The live ticker bar (ornamental, not informational)
//   - "Save View" / sliders / bell / "Live · NY Open" status
//   - The "INSTITUTIONAL · v0.1" sub-tagline
// Keep:
//   - The ⌘K / search affordance — but routes the user to /ask via
//     SPA navigation rather than "focus copilot composer".
// ============================================================================

import { Link, NavLink, useLocation } from 'react-router-dom';
import { Search, User } from 'lucide-react';
import { cn } from '@/utils/cn';

type SurfaceDef = {
  label: string;
  /** Primary route the surface anchors to. */
  to: string;
  /** Routes that should ALSO mark this surface active.  Used to keep
   *  the existing /rates, /tools, /workflows pages glowing under their
   *  proper surface during the IA transition. */
  alsoActiveOn?: string[];
};

// Order from the revamp brief.  Ask sits in the second slot —
// product's editorial centerpiece.
const SURFACES: SurfaceDef[] = [
  {
    label: 'Monitor',
    to: '/',
    alsoActiveOn: ['/rates', '/fx', '/policy', '/events'],
  },
  { label: 'Ask', to: '/ask' },
  { label: 'Build', to: '/workspace' },
  {
    label: 'Library',
    to: '/tools',
    // Workflows page is conceptually "Library → Templates" — same
    // surface even though the URL hasn't migrated yet.
    alsoActiveOn: ['/workflows'],
  },
  { label: 'Briefcase', to: '/briefcase' },
];

export function TopNav() {
  const location = useLocation();

  return (
    <header className="relative flex h-[52px] shrink-0 items-center gap-6 border-b border-line-subtle px-6">
      {/* Brand */}
      <Link
        to="/"
        className="flex shrink-0 items-center gap-2.5"
      >
        <span className="relative flex h-6 w-6 items-center justify-center rounded-[5px] border border-line-soft bg-gradient-to-br from-ice-400/30 via-ice-700/30 to-ink-700">
          <span className="h-2.5 w-2.5 rounded-[2px] bg-gradient-to-br from-white to-ice-200 shadow-[0_0_10px_rgba(122,162,255,0.35)]" />
        </span>
        <span className="text-[13.5px] font-medium tracking-[-0.01em] text-fg-primary">
          macro copilot
        </span>
      </Link>

      {/* Spacer */}
      <span className="h-3 w-px bg-line-soft" />

      {/* Surface tabs */}
      <nav className="flex items-center gap-1">
        {SURFACES.map((s) => (
          <SurfaceTab key={s.to} surface={s} pathname={location.pathname} />
        ))}
      </nav>

      {/* Search → routes to /ask, keeping the ⌘K muscle memory.  We
          intentionally do NOT focus a chat drawer here anymore. */}
      <div className="ml-auto flex items-center gap-3">
        <Link
          to="/ask"
          className="flex w-[260px] items-center gap-2.5 rounded-md border border-line-soft bg-white/[0.012] px-3 py-1.5 text-[12px] text-fg-muted transition-colors duration-150 ease-sleek hover:border-line-strong hover:bg-white/[0.025] hover:text-fg-secondary"
        >
          <Search size={12} />
          <span className="flex-1 truncate text-left">Ask the copilot…</span>
          <kbd className="hidden rounded border border-line-soft bg-white/[0.02] px-1 py-px text-[9px] font-medium text-fg-muted lg:inline-flex">
            ⌘K
          </kbd>
        </Link>

        {/* Profile avatar */}
        <button
          type="button"
          aria-label="Profile"
          className="flex h-7 w-7 items-center justify-center rounded-full border border-line-soft bg-gradient-to-br from-ice-500/30 to-ice-700/40 text-fg-muted transition-colors hover:text-fg-secondary"
        >
          <User size={11} />
        </button>
      </div>
    </header>
  );
}

function SurfaceTab({
  surface,
  pathname,
}: {
  surface: SurfaceDef;
  pathname: string;
}) {
  const isPrimaryActive =
    surface.to === '/' ? pathname === '/' : pathname.startsWith(surface.to);
  const isSecondaryActive = surface.alsoActiveOn?.some((p) =>
    p === '/' ? pathname === '/' : pathname.startsWith(p),
  );
  const isActive = isPrimaryActive || isSecondaryActive;

  return (
    <NavLink
      to={surface.to}
      end={surface.to === '/'}
      className={cn(
        'relative flex h-[52px] items-center px-3 text-[13px] font-medium tracking-[-0.005em] transition-colors duration-150 ease-sleek',
        isActive
          ? 'text-fg-primary'
          : 'text-fg-muted hover:text-fg-secondary',
      )}
    >
      <span>{surface.label}</span>
      {isActive && (
        <span className="absolute inset-x-3 -bottom-px h-[2px] rounded-t-full bg-ice-400 shadow-[0_0_10px_rgba(122,162,255,0.4)]" />
      )}
    </NavLink>
  );
}
