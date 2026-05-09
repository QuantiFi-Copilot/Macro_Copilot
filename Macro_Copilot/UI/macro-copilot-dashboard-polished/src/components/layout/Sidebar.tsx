// ============================================================================
// Sidebar — left navigation rail for non-Ask surfaces
// ----------------------------------------------------------------------------
// Two sections:
//   - WORKSPACE: Home (links to /, the Monitor surface)
//   - AGENTS:    Rates Agent (live, links to /rates) + 5 dimmed
//                placeholders (FX, Credit, Macro Equity, Policy/Events,
//                PM Orchestrator).
//
// V0 had a Library section + Saved Views — both dropped here.  Library
// is now in the top nav (single source of truth for navigation), and
// Saved Views is a V2 surface (no V1 stub).
//
// Visual register matches the Ask page: same gradient sweep on active
// rows, soft hover, status dots keyed to agent maturity (live / dev).
// ============================================================================

import type { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  Activity,
  Bot,
  ChevronRight,
  Gauge,
  Home,
  Layers,
  LineChart,
  Settings,
  ShieldCheck,
} from 'lucide-react';
import { cn } from '@/utils/cn';

type AgentItem = {
  label: string;
  to: string;
  icon: ReactNode;
  status: 'live' | 'dev';
};

const AGENTS: AgentItem[] = [
  { label: 'Rates Agent', to: '/rates', icon: <Layers size={13} />, status: 'live' },
  { label: 'FX Agent', to: '/fx', icon: <LineChart size={13} />, status: 'dev' },
  { label: 'Credit Agent', to: '/credit', icon: <Activity size={13} />, status: 'dev' },
  { label: 'Macro Equity', to: '/macro-equity', icon: <Gauge size={13} />, status: 'dev' },
  { label: 'Policy / Events', to: '/policy', icon: <ShieldCheck size={13} />, status: 'dev' },
  { label: 'PM Orchestrator', to: '/pm-orchestrator', icon: <Bot size={13} />, status: 'dev' },
];

export function Sidebar() {
  const location = useLocation();
  const path = location.pathname;

  return (
    <aside className="relative flex h-full min-h-0 flex-col overflow-hidden border-r border-line-subtle">
      {/* No top-of-rail search button: the TopNav already exposes the
          same ⌘K → /ask affordance.  Two search buttons in the same
          column became visual noise once TopNav was lifted to a
          global position above the sidebar. */}

      <nav className="mt-5 flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-3 pb-4">
        {/* WORKSPACE section */}
        <Section label="WORKSPACE">
          <NavRow
            icon={<Home size={13} />}
            label="Home"
            to="/"
            active={path === '/'}
          />
        </Section>

        {/* AGENTS section */}
        <Section label="AGENTS">
          {AGENTS.map((agent) => (
            <AgentRow
              key={agent.to}
              agent={agent}
              active={path.startsWith(agent.to)}
            />
          ))}
        </Section>
      </nav>

      {/* Footer — user identity */}
      <div className="mx-4 border-t border-line-subtle" />
      <div className="flex items-center justify-between gap-3 px-4 py-3.5">
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
    </aside>
  );
}

// ----------------------------------------------------------------------------

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
          ? // Same gradient sweep used on Ask's threads rail — keeps
            // the active-row visual language consistent.
            'bg-[linear-gradient(90deg,rgba(122,162,255,0.08)_0%,rgba(122,162,255,0.025)_55%,transparent_100%)] text-fg-primary shadow-[inset_0_1px_0_rgba(255,255,255,0.04),inset_0_0_0_1px_rgba(122,162,255,0.16)]'
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

function AgentRow({ agent, active }: { agent: AgentItem; active: boolean }) {
  const isLive = agent.status === 'live';

  const statusDot = isLive
    ? 'bg-mint-400 shadow-[0_0_8px_rgba(63,214,154,0.5)]'
    : 'bg-fg-faint';

  return (
    <Link
      to={agent.to}
      className={cn(
        'group relative flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[12.5px] font-medium tracking-[-0.005em] transition-all duration-200 ease-sleek',
        active
          ? 'bg-[linear-gradient(90deg,rgba(122,162,255,0.08)_0%,rgba(122,162,255,0.025)_55%,transparent_100%)] text-fg-primary shadow-[inset_0_1px_0_rgba(255,255,255,0.04),inset_0_0_0_1px_rgba(122,162,255,0.16)]'
          : isLive
            ? 'text-fg-secondary hover:bg-white/[0.022] hover:text-fg-primary'
            : // Dimmed state for placeholder agents — visible but
              // clearly secondary.
              'text-fg-faint hover:bg-white/[0.018] hover:text-fg-muted',
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
          active ? 'text-ice-300' : isLive ? 'text-fg-muted' : 'text-fg-faint',
        )}
      >
        {agent.icon}
      </span>
      <span className="flex-1 truncate">{agent.label}</span>
      <span className={cn('h-1.5 w-1.5 rounded-full', statusDot)} />
      {!isLive && (
        <span className="hidden font-mono text-[8.5px] uppercase tracking-[0.14em] text-fg-faint xl:inline">
          soon
        </span>
      )}
      {isLive && !active && (
        <ChevronRight
          size={11}
          className="text-fg-faint opacity-0 transition-opacity group-hover:opacity-60"
        />
      )}
    </Link>
  );
}
