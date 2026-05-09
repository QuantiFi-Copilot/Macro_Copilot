// ============================================================================
// Sidebar — left navigation rail for widget surfaces
// ----------------------------------------------------------------------------
// Composition (top → bottom):
//   1. WORKSPACE   — Home (single row)
//   2. AGENTS      — six rows, two-line each.  Live agents (Rates) are
//                    expandable with a chevron and reveal sub-items
//                    (sovereign bonds, OIS, swap spreads, saved
//                    analyses).  Dimmed agents show their planned
//                    scope as a single subtext line, no expansion.
//   3. TODAY       — small live-state panel: market session, scanner
//                    summary, last data refresh.  Reads from the
//                    RatesDataContext mounted at AppShell.
//   4. status      — connection ribbon above the user identity
//   5. user footer — avatar + role
//
// Each section earns its space: agent rows carry scope info, Today
// surfaces real signal, status confirms connectivity.  No padding-
// only filler.
// ============================================================================

import type { ReactNode } from 'react';
import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  Activity,
  Bot,
  Bookmark,
  ChevronRight,
  Circle,
  Clock,
  Gauge,
  Home,
  Layers,
  LineChart,
  Radar,
  Settings,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { cn } from '@/utils/cn';
import { useOptionalRatesDataContext } from '@/components/monitor/RatesDataProvider';

// ----------------------------------------------------------------------------
// Agent registry — single source of truth for sidebar agent rows.
// `subItems` is non-empty only on live agents (currently just Rates),
// since dimmed agents have nothing to drill into.

type AgentSubItem = {
  label: string;
  /** When set, the row is clickable and routes here.  When omitted,
   *  the row is decorative — used for V2 placeholders like "Saved
   *  analyses". */
  to?: string;
  /** Shown as a tiny right-aligned hint when the row is decorative. */
  hint?: string;
};

type AgentItem = {
  label: string;
  to: string;
  icon: ReactNode;
  status: 'live' | 'dev';
  /** One-line scope subtext — drives row height and signals what the
   *  agent covers.  For live agents we keep it factual; for dimmed
   *  ones we name the planned scope (matches the placeholder pages). */
  scope: string;
  /** Sub-items revealed when the row is expanded.  Only set on live
   *  agents. */
  subItems?: AgentSubItem[];
};

// Subtext budget: the row is ~136px wide for content after icon /
// gap / status dot / chevron.  At 9.5px mono that's ≈ 22 chars max
// before truncation kicks in.  Keep all scope strings under that
// budget — earlier laundry-list copy ("G10 · EM · NDFs · vol
// surfaces") was overflowing the column and making the whole sidebar
// horizontally scrollable.
//
// Live agents render real-data subtext (count of curves / tenors)
// derived in the Sidebar component below; the static `scope` here is
// a fallback used only while data is loading.

const AGENTS: AgentItem[] = [
  {
    label: 'Rates Agent',
    to: '/rates',
    icon: <Layers size={13} />,
    status: 'live',
    // Replaced at render-time with "{N} curves · {M} tenors" once
    // rates data resolves.  Until then this static fallback shows.
    scope: 'Sovereign + OIS',
    subItems: [
      // V1: sub-items all link to the Rates Agent page.  V2 will read
      // a `?scope=...` query param to filter the widget catalog to
      // that subset; for now they're navigation anchors that put the
      // user on the rates surface.
      { label: 'Sovereign Bonds', to: '/rates' },
      { label: 'OIS', to: '/rates' },
      { label: 'Swap Spreads', to: '/rates' },
      { label: 'Saved analyses', hint: 'V2' },
    ],
  },
  {
    label: 'FX Agent',
    to: '/fx',
    icon: <LineChart size={13} />,
    status: 'dev',
    scope: 'G10 · EM',
  },
  {
    label: 'Credit Agent',
    to: '/credit',
    icon: <Activity size={13} />,
    status: 'dev',
    scope: 'IG · HY',
  },
  {
    label: 'Macro Equity',
    to: '/macro-equity',
    icon: <Gauge size={13} />,
    status: 'dev',
    scope: 'Indices · factors',
  },
  {
    label: 'Policy / Events',
    to: '/policy',
    icon: <ShieldCheck size={13} />,
    status: 'dev',
    scope: 'WIRP · calendar',
  },
  {
    label: 'PM Orchestrator',
    to: '/pm-orchestrator',
    icon: <Bot size={13} />,
    status: 'dev',
    scope: 'Cross-agent',
  },
];

// ----------------------------------------------------------------------------

export function Sidebar() {
  const { pathname } = useLocation();

  // Expand state — keyed by agent.to.  An agent is expanded by
  // default if the user is currently on its page; users can manually
  // collapse / expand once they're navigating elsewhere.  We seed the
  // map every render with the active-route default so navigation
  // auto-expands the right agent without trapping the user in a
  // stale collapsed state.
  const [manualExpand, setManualExpand] = useState<Record<string, boolean>>({});

  // Live scope for the Rates Agent row, derived from the same rates
  // context the Today panel reads.  `useOptionalRatesDataContext`
  // returns null when no provider is mounted (e.g. on the legacy
  // three-column layout if it skips the wrapper) so the sidebar
  // degrades to its static scope strings instead of crashing the
  // entire page.
  const ratesCtx = useOptionalRatesDataContext();
  const data = ratesCtx?.data ?? null;
  const ratesLiveScope = data
    ? `${data.yieldSnapshot.curve_families.length} curves · ${data.yieldSnapshot.tenors.length} tenors`
    : null;

  return (
    <aside className="relative flex h-full min-h-0 flex-col overflow-hidden border-r border-line-subtle">
      {/* Scrollable nav area — sections stack here, footer pinned below.
          `overflow-x-hidden` is explicit: nav allows vertical scroll
          but never horizontal.  Combined with `min-w-0` on each
          AgentRow, this guarantees long subtexts truncate cleanly
          rather than triggering a horizontal scrollbar. */}
      <nav className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto overflow-x-hidden px-3 pb-4 pt-5">
        <Section label="WORKSPACE">
          <NavRow
            icon={<Home size={13} />}
            label="Home"
            to="/"
            active={pathname === '/'}
          />
        </Section>

        <Section label="AGENTS">
          {AGENTS.map((agent) => {
            const isActive = pathname.startsWith(agent.to);
            const isExpanded =
              manualExpand[agent.to] ??
              // Default: live agents auto-expand on their own route.
              (agent.status === 'live' && isActive);
            // Substitute live data for the Rates Agent's scope when
            // available; other agents always use their static scope.
            const resolvedAgent =
              agent.to === '/rates' && ratesLiveScope
                ? { ...agent, scope: ratesLiveScope }
                : agent;
            return (
              <AgentRow
                key={agent.to}
                agent={resolvedAgent}
                active={isActive}
                expanded={isExpanded}
                pathname={pathname}
                onToggleExpand={() =>
                  setManualExpand((prev) => ({
                    ...prev,
                    [agent.to]: !isExpanded,
                  }))
                }
              />
            );
          })}
        </Section>

        <Section label="TODAY">
          <TodayPanel />
        </Section>
      </nav>

      {/* Status ribbon + user identity footer.  The mx-3 hairline ties
          the section off cleanly; ribbon and identity sit below. */}
      <div className="mx-3 border-t border-line-subtle" />
      <StatusRibbon />
      <UserFooter />
    </aside>
  );
}

// ----------------------------------------------------------------------------
// Section header

function Section({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div>
      <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-fg-faint">
        {label}
      </p>
      <div className="space-y-px">{children}</div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Workspace nav row — single-line, simple

function NavRow({
  icon,
  label,
  to,
  active,
}: {
  icon: ReactNode;
  label: string;
  to: string;
  active: boolean;
}) {
  return (
    <Link
      to={to}
      className={cn(
        'group relative flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[12.5px] font-medium tracking-[-0.005em] transition-all duration-200 ease-sleek',
        active
          ? 'bg-[linear-gradient(90deg,rgba(122,162,255,0.08)_0%,rgba(122,162,255,0.025)_55%,transparent_100%)] text-fg-primary shadow-[inset_0_1px_0_rgba(255,255,255,0.04),inset_0_0_0_1px_rgba(122,162,255,0.16)]'
          : 'text-fg-secondary hover:bg-white/[0.022] hover:text-fg-primary',
      )}
    >
      {active && (
        <span
          aria-hidden
          className="absolute inset-y-1.5 left-0 w-[2px] rounded-r-full bg-gradient-to-b from-transparent via-ice-400 to-transparent shadow-[0_0_10px_rgba(122,162,255,0.45)]"
        />
      )}
      <span
        className={cn(
          'flex h-4 w-4 items-center justify-center transition-colors',
          active ? 'text-ice-300' : 'text-fg-muted group-hover:text-fg-secondary',
        )}
      >
        {icon}
      </span>
      <span className="flex-1 truncate">{label}</span>
    </Link>
  );
}

// ----------------------------------------------------------------------------
// Agent row — two-line.  Live agents have a chevron toggle + sub-item
// drawer; dimmed agents show their scope subtext but no expansion.

function AgentRow({
  agent,
  active,
  expanded,
  pathname,
  onToggleExpand,
}: {
  agent: AgentItem;
  active: boolean;
  expanded: boolean;
  pathname: string;
  onToggleExpand: () => void;
}) {
  const isLive = agent.status === 'live';
  const hasSubItems = !!agent.subItems && agent.subItems.length > 0;
  const showSubItems = isLive && hasSubItems && expanded;

  const statusDot = isLive
    ? 'bg-mint-400 shadow-[0_0_8px_rgba(63,214,154,0.5)]'
    : 'bg-fg-faint';

  return (
    <div className="space-y-px">
      <div
        // `min-w-0` is critical here: the outer flex container will
        // otherwise size to its content's intrinsic width and push
        // the whole row past the sidebar column width — causing the
        // page to gain a horizontal scrollbar.  With min-w-0 the
        // flex container respects the parent's width and the inner
        // truncate spans actually clip.
        className={cn(
          'group relative flex w-full min-w-0 items-stretch rounded-lg transition-all duration-200 ease-sleek',
          active
            ? 'bg-[linear-gradient(90deg,rgba(122,162,255,0.08)_0%,rgba(122,162,255,0.025)_55%,transparent_100%)] shadow-[inset_0_1px_0_rgba(255,255,255,0.04),inset_0_0_0_1px_rgba(122,162,255,0.16)]'
            : isLive
              ? 'hover:bg-white/[0.022]'
              : 'hover:bg-white/[0.014]',
        )}
      >
        {active && (
          <span
            aria-hidden
            className="absolute inset-y-1.5 left-0 w-[2px] rounded-r-full bg-gradient-to-b from-transparent via-ice-400 to-transparent shadow-[0_0_10px_rgba(122,162,255,0.45)]"
          />
        )}

        {/* Main link — fills the row.  `min-w-0` again so the inner
            label/scope spans truncate inside the link rather than
            forcing the link wider.  Sub-item chevron sits to the
            right of the link as a separate button so clicks on the
            chevron don't navigate. */}
        <Link
          to={agent.to}
          className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2 text-left"
        >
          <span
            className={cn(
              'flex h-4 w-4 shrink-0 items-center justify-center transition-colors',
              active
                ? 'text-ice-300'
                : isLive
                  ? 'text-fg-muted'
                  : 'text-fg-faint',
            )}
          >
            {agent.icon}
          </span>
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span
              className={cn(
                'truncate text-[12.5px] font-medium tracking-[-0.005em] transition-colors',
                active
                  ? 'text-fg-primary'
                  : isLive
                    ? 'text-fg-secondary group-hover:text-fg-primary'
                    : 'text-fg-faint',
              )}
            >
              {agent.label}
            </span>
            <span
              className={cn(
                'truncate font-mono text-[9.5px] tracking-[0.02em]',
                active ? 'text-fg-muted' : 'text-fg-faint',
              )}
            >
              {agent.scope}
            </span>
          </span>
          <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', statusDot)} />
          {!isLive && (
            <span className="hidden font-mono text-[8.5px] uppercase tracking-[0.14em] text-fg-faint xl:inline">
              soon
            </span>
          )}
        </Link>

        {/* Chevron toggle — only on live, expandable agents.  Sized
            to match the row height; clicking it doesn't navigate. */}
        {isLive && hasSubItems && (
          <button
            type="button"
            onClick={onToggleExpand}
            aria-label={expanded ? 'Collapse' : 'Expand'}
            aria-expanded={expanded}
            className="flex w-7 items-center justify-center rounded-r-lg text-fg-faint transition-colors hover:bg-white/[0.025] hover:text-fg-secondary"
          >
            <ChevronRight
              size={11}
              className={cn(
                'transition-transform duration-200 ease-sleek',
                expanded && 'rotate-90',
              )}
            />
          </button>
        )}
      </div>

      {/* Sub-item drawer */}
      {showSubItems && (
        <div className="ml-5 space-y-px border-l border-line-subtle pl-3 py-1">
          {agent.subItems!.map((sub) => (
            <SubItemRow key={sub.label} item={sub} pathname={pathname} />
          ))}
        </div>
      )}
    </div>
  );
}

function SubItemRow({
  item,
  pathname,
}: {
  item: AgentSubItem;
  pathname: string;
}) {
  // Decorative sub-items (no `to`) render as plain text rows with a
  // soft hint pill on the right.  Linkable sub-items render as Link
  // and pick up the standard active style if the route matches.
  if (!item.to) {
    return (
      <div className="flex items-center justify-between gap-2 rounded-md px-2 py-1.5">
        <span className="text-[11.5px] tracking-[-0.005em] text-fg-faint">
          {item.label}
        </span>
        {item.hint && (
          <span className="font-mono text-[9px] uppercase tracking-[0.14em] text-fg-faint/80">
            {item.hint}
          </span>
        )}
      </div>
    );
  }

  // V1: every linkable sub-item routes to /rates, so "active" simply
  // mirrors whether the user is on /rates.  V2 will pass a scope
  // query param and use that for finer-grained active-state.
  const active = pathname.startsWith(item.to);
  return (
    <Link
      to={item.to}
      className={cn(
        'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[11.5px] tracking-[-0.005em] transition-colors',
        active
          ? 'text-fg-primary'
          : 'text-fg-muted hover:bg-white/[0.022] hover:text-fg-secondary',
      )}
    >
      <span className="text-fg-faint">·</span>
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

// ----------------------------------------------------------------------------
// TODAY panel — three rows of live state.  Reads from the rates
// context mounted by AppShell; degrades gracefully when data is
// loading or errored.

function TodayPanel() {
  // Optional context — sidebar may be rendered on routes that don't
  // mount RatesDataProvider (legacy 3-col layout).  Treat null ctx
  // as "no rates data available" and render neutral states.
  const ctx = useOptionalRatesDataContext();
  const data = ctx?.data ?? null;
  const isLoading = ctx?.isLoading ?? false;
  const error = ctx?.error ?? null;
  const session = useNySession();

  // Scanner summary — match the Monitor headline's wording.
  const flaggedCount = data?.scanner.results.length ?? null;
  let scannerLine: string;
  if (error && !data) {
    scannerLine = 'Scanner unavailable';
  } else if (flaggedCount === null) {
    scannerLine = 'Scanning…';
  } else if (flaggedCount === 0) {
    scannerLine = 'Markets quiet · ≥ 1.5σ';
  } else {
    // Same hedge as MonitorPage when count hits the cap.
    const isAtCap = flaggedCount >= 8;
    scannerLine = `${isAtCap ? '≥ ' : ''}${flaggedCount} above 1.5σ`;
  }

  // Last refresh — derived from any rates row's as_of_date.  The
  // backend returns YYYY-MM-DD so we render a date, not a time.
  const asOf = data?.yieldSnapshot.rows[0]?.as_of_date ?? null;
  const refreshLine = asOf
    ? `${formatRelativeDate(asOf)} · TimescaleDB`
    : isLoading
      ? 'Loading…'
      : 'No data';

  return (
    <div className="space-y-1">
      <TodayRow
        icon={<Clock size={11} />}
        label="Session"
        value={session.label}
        tone={session.isOpen ? 'mint' : 'neutral'}
      />
      <TodayRow
        icon={<Radar size={11} />}
        label="Scanner"
        value={scannerLine}
        tone={
          flaggedCount === null || error
            ? 'neutral'
            : flaggedCount === 0
              ? 'mint'
              : 'amber'
        }
      />
      <TodayRow
        icon={<Sparkles size={11} />}
        label="Last refresh"
        value={refreshLine}
        tone="neutral"
      />
    </div>
  );
}

function TodayRow({
  icon,
  label,
  value,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  tone: 'mint' | 'amber' | 'neutral';
}) {
  const dotClass =
    tone === 'mint'
      ? 'bg-mint-400 shadow-[0_0_6px_rgba(63,214,154,0.5)]'
      : tone === 'amber'
        ? 'bg-amber-400 shadow-[0_0_6px_rgba(243,183,85,0.5)]'
        : 'bg-fg-faint';
  return (
    <div className="flex items-center gap-2 rounded-md px-3 py-1.5">
      <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', dotClass)} />
      <span className="flex h-4 w-4 shrink-0 items-center justify-center text-fg-faint">
        {icon}
      </span>
      <span className="flex min-w-0 flex-1 flex-col leading-tight">
        <span className="truncate text-[10.5px] font-medium uppercase tracking-[0.12em] text-fg-faint">
          {label}
        </span>
        <span className="truncate font-mono text-[10.5px] tracking-[0.02em] text-fg-secondary">
          {value}
        </span>
      </span>
    </div>
  );
}

// ----------------------------------------------------------------------------
// STATUS RIBBON — single line above the user footer.  Connection +
// system summary, derived from the rates context.

function StatusRibbon() {
  // Optional context — sidebar may be rendered on routes without a
  // mounted provider; degrade to neutral "context unavailable" copy
  // rather than crashing.
  const ctx = useOptionalRatesDataContext();
  const data = ctx?.data ?? null;
  const isLoading = ctx?.isLoading ?? false;
  const error = ctx?.error ?? null;

  let dot: 'mint' | 'amber' | 'coral' | 'neutral';
  let text: string;
  if (!ctx) {
    // No provider on this route — Sidebar is rendered but the
    // surface doesn't fetch rates data.  Show a neutral indicator.
    dot = 'neutral';
    text = 'Workspace · TimescaleDB';
  } else if (error && !data) {
    dot = 'coral';
    text = 'Disconnected';
  } else if (!data || isLoading) {
    dot = 'amber';
    text = 'Connecting · TimescaleDB';
  } else {
    dot = 'mint';
    const curveCount = data.yieldSnapshot.curve_families.length;
    text = `Connected · TimescaleDB · ${curveCount} curves live`;
  }

  const dotClass =
    dot === 'mint'
      ? 'bg-mint-400 shadow-[0_0_6px_rgba(63,214,154,0.55)]'
      : dot === 'amber'
        ? 'bg-amber-400 shadow-[0_0_6px_rgba(243,183,85,0.55)]'
        : dot === 'coral'
          ? 'bg-coral-400 shadow-[0_0_6px_rgba(255,107,126,0.55)]'
          : 'bg-fg-faint';

  return (
    <div className="flex items-center gap-2 px-4 py-2">
      <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', dotClass)} />
      <span className="truncate font-mono text-[10px] tracking-[0.02em] text-fg-muted">
        {text}
      </span>
    </div>
  );
}

// ----------------------------------------------------------------------------
// USER FOOTER — identity + settings cog.  Sits below the StatusRibbon;
// the parent already renders the hairline divider above the ribbon.

function UserFooter() {
  return (
    <div className="flex items-center justify-between gap-3 px-4 pb-3.5 pt-2">
      <div className="flex min-w-0 items-center gap-2.5">
        <div className="relative h-7 w-7 rounded-full bg-gradient-to-br from-ice-500/40 to-ice-700/50 ring-1 ring-line-strong">
          <div className="absolute bottom-0 right-0 h-2 w-2 rounded-full bg-mint-400 ring-2 ring-ink-900" />
        </div>
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-[12px] font-medium text-fg-primary">
            S. Mandra
          </span>
          <span className="truncate font-mono text-[10px] text-fg-faint">
            Pod · Discretionary Macro
          </span>
        </div>
      </div>
      <button
        type="button"
        className="rounded-md p-1.5 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary"
        aria-label="Settings"
      >
        <Settings size={13} />
      </button>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Helpers

/** Resolves a rough NY-time market session label.  Conservative: uses
 *  the user's local clock with a UTC offset hint, since we don't have
 *  an actual exchange-calendar primitive on the front-end.  Good
 *  enough for an at-a-glance label in the rail. */
function useNySession(): { label: string; isOpen: boolean } {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 60_000);
    return () => clearInterval(id);
  }, []);
  void tick; // forces re-render every minute so the label drifts naturally

  const now = new Date();
  // NY = UTC-5 standard / UTC-4 DST.  Use UTC-5 as a safe default;
  // off-by-one hour during DST is acceptable for an at-a-glance
  // session indicator.  V2 can wire a proper calendar primitive.
  const utc = now.getUTCHours() + now.getUTCMinutes() / 60;
  const nyHour = (utc - 5 + 24) % 24;

  // RTH: 09:30 – 16:00 NY
  const rthOpen = nyHour >= 9.5 && nyHour < 16;
  // Pre-market 04:00 – 09:30; after-hours 16:00 – 20:00
  const isPre = nyHour >= 4 && nyHour < 9.5;
  const isAfter = nyHour >= 16 && nyHour < 20;

  const hh = String(now.getUTCHours()).padStart(2, '0');
  const mm = String(now.getUTCMinutes()).padStart(2, '0');
  const utcStamp = `${hh}:${mm} UTC`;

  if (rthOpen) return { label: `NY open · ${utcStamp}`, isOpen: true };
  if (isPre) return { label: `Pre-market · ${utcStamp}`, isOpen: true };
  if (isAfter) return { label: `After-hours · ${utcStamp}`, isOpen: true };
  return { label: `NY closed · ${utcStamp}`, isOpen: false };
}

/** "today", "yesterday", or "N days ago".  Used by the Today panel's
 *  Last refresh row.  The backend ships YYYY-MM-DD strings; we parse
 *  to a local Date and diff against today. */
function formatRelativeDate(asOfDate: string): string {
  const today = new Date();
  const todayUTC = Date.UTC(
    today.getUTCFullYear(),
    today.getUTCMonth(),
    today.getUTCDate(),
  );
  const [yy, mm, dd] = asOfDate.split('-').map(Number);
  if (!yy || !mm || !dd) return asOfDate;
  const asOfUTC = Date.UTC(yy, mm - 1, dd);
  const days = Math.round((todayUTC - asOfUTC) / (1000 * 60 * 60 * 24));
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days > 0 && days < 7) return `${days}d ago`;
  return asOfDate;
}

// Suppress the 'Bookmark' / 'Circle' import warnings — kept imported
// for use as future TODAY / status icons (e.g. when alerts ship).
void Bookmark;
void Circle;
