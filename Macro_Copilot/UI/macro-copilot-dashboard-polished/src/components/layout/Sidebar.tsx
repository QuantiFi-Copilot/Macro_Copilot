import type { ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import {
  Activity,
  BarChart3,
  Bot,
  CalendarClock,
  ChevronRight,
  CircleDot,
  Command,
  Gauge,
  Layers,
  LineChart,
  Settings,
  ShieldCheck,
} from 'lucide-react';
import { cn } from '@/utils/cn';
import type { SidebarGroup } from '@/types/dashboard';

type SidebarProps = {
  groups: SidebarGroup[];
};

const AGENT_META = [
  {
    icon: <Layers size={14} />,
    label: 'Rates Agent',
    status: 'live' as const,
    path: '/rates',
  },
  {
    icon: <LineChart size={14} />,
    label: 'FX Agent',
    status: 'live' as const,
    path: '/fx',
  },
  {
    icon: <Activity size={14} />,
    label: 'Credit Agent',
    status: 'beta' as const,
    path: '/credit',
  },
  {
    icon: <Gauge size={14} />,
    label: 'Macro Equity',
    status: 'beta' as const,
    path: '/macro-equity',
  },
  {
    icon: <ShieldCheck size={14} />,
    label: 'Policy / Events',
    status: 'live' as const,
    path: '/policy',
  },
  {
    icon: <Bot size={14} />,
    label: 'PM Orchestrator',
    status: 'dev' as const,
    path: '/workspace',
  },
];

const FX_SUB_ITEMS = [
  'Daily Monitor',
  'Spot Scanner',
  'Carry Monitor',
  'Vol Monitor',
  'Chat with FX Agent',
];

export function Sidebar({ groups }: SidebarProps) {
  const location = useLocation();
  const primaryGroup = groups[0];

  const isRatesPath = location.pathname.startsWith('/rates');
  const isFxPath = location.pathname.startsWith('/fx');

  return (
    <aside className="panel relative flex h-full min-h-0 flex-col">
      <div className="panel-divider-r absolute right-0 top-0 h-full w-px" />

      {/* Brand */}
      <div className="flex h-[64px] items-center gap-3 px-5">
        <div className="relative flex h-8 w-8 items-center justify-center rounded-[9px] border border-line-soft bg-gradient-to-br from-ice-400/30 via-ice-700/30 to-ink-700 shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]">
          <div className="h-3.5 w-3.5 rounded-[3px] bg-gradient-to-br from-white to-ice-200 shadow-[0_0_14px_rgba(122,162,255,0.35)]" />
        </div>
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-[13.5px] font-semibold tracking-[-0.01em] text-fg-primary">
            Macro Copilot
          </span>
          <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-fg-muted">
            Institutional · v0.1
          </span>
        </div>
      </div>

      <div className="mx-5 border-b border-line-subtle" />

      {/* Workspace search */}
      <div className="px-4 pt-4">
        <button
          type="button"
          className="flex w-full items-center gap-2.5 rounded-lg border border-line-soft bg-white/[0.02] px-3 py-2 text-[12px] text-fg-muted transition-colors duration-150 ease-sleek hover:border-line-strong hover:bg-white/[0.035] hover:text-fg-secondary"
        >
          <Command size={13} className="text-fg-muted" />
          <span className="flex-1 text-left">Search workspace</span>
          <kbd className="rounded border border-line-soft bg-white/[0.03] px-1.5 py-[1px] text-[9px] font-medium text-fg-muted">
            ⌘K
          </kbd>
        </button>
      </div>

      {/* Scroll area */}
      <nav className="mt-4 flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-3 pb-4">
        <SectionLabel>Workspace</SectionLabel>
        <div className="-mt-3 space-y-0.5 px-1">
          <NavRow icon={<CircleDot size={14} />} label="Home" active={location.pathname === '/'} />
          <NavRow icon={<BarChart3 size={14} />} label="Daily Monitor" active={location.pathname === '/'} />
          <NavRow icon={<CalendarClock size={14} />} label="Event Calendar" />
        </div>

        {/* Agents */}
        <div>
          <SectionLabel>Agents</SectionLabel>
          <div className="mt-1 space-y-0.5 px-1">
            {AGENT_META.map((agent) => {
              const active =
                agent.path === '/rates'
                  ? isRatesPath
                  : agent.path === '/fx'
                    ? isFxPath
                    : location.pathname.startsWith(agent.path);

              const subItems =
                agent.path === '/rates'
                  ? primaryGroup?.items.map((item) => item.label)
                  : agent.path === '/fx'
                    ? FX_SUB_ITEMS
                    : undefined;

              return (
                <AgentRow
                  key={agent.label}
                  icon={agent.icon}
                  label={agent.label}
                  status={agent.status}
                  active={active}
                  expanded={active}
                  subItems={subItems}
                />
              );
            })}
          </div>
        </div>

        <div>
          <SectionLabel>Saved Views</SectionLabel>
          <div className="mt-1 space-y-0.5 px-1">
            <NavRow icon={<Dot />} label="G10 Curves · 2s10s" muted />
            <NavRow icon={<Dot />} label="CPI Playbook · US" muted />
            <NavRow icon={<Dot />} label="EUR Real Yields" muted />
          </div>
        </div>
      </nav>

      {/* Footer */}
      <div className="mx-5 border-t border-line-subtle" />
      <div className="flex items-center justify-between gap-3 px-5 py-3.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="relative h-7 w-7 rounded-full bg-gradient-to-br from-ice-500/40 to-ice-700/50 ring-1 ring-line-strong">
            <div className="absolute bottom-0 right-0 h-2 w-2 rounded-full bg-mint-400 ring-2 ring-ink-900" />
          </div>
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="truncate text-[12px] font-medium text-fg-primary">S. Mandra</span>
            <span className="truncate text-[10px] text-fg-muted">Pod · Discretionary Macro</span>
          </div>
        </div>
        <button
          type="button"
          className="rounded-md p-1.5 text-fg-muted transition-colors hover:bg-white/[0.04] hover:text-fg-secondary"
        >
          <Settings size={14} />
        </button>
      </div>
    </aside>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="px-3 pt-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-fg-faint">
      {children}
    </div>
  );
}

function Dot() {
  return <span className="block h-1 w-1 rounded-full bg-fg-faint" />;
}

function NavRow({
  icon,
  label,
  active,
  muted,
  trailing,
}: {
  icon: ReactNode;
  label: string;
  active?: boolean;
  muted?: boolean;
  trailing?: ReactNode;
}) {
  return (
    <button
      type="button"
      className={cn(
        'group relative flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[12.5px] font-medium tracking-[-0.005em] transition-colors duration-150 ease-sleek',
        active
          ? 'nav-active-rail bg-white/[0.035] text-fg-primary'
          : muted
            ? 'text-fg-muted hover:bg-white/[0.02] hover:text-fg-secondary'
            : 'text-fg-secondary hover:bg-white/[0.025] hover:text-fg-primary',
      )}
    >
      <span
        className={cn(
          'flex h-4 w-4 items-center justify-center transition-colors',
          active ? 'text-ice-300' : 'text-fg-muted group-hover:text-fg-secondary',
        )}
      >
        {icon}
      </span>
      <span className="flex-1 truncate">{label}</span>
      {trailing}
    </button>
  );
}

function AgentRow({
  icon,
  label,
  status,
  active,
  expanded,
  subItems,
}: {
  icon: ReactNode;
  label: string;
  status: 'live' | 'beta' | 'dev';
  active?: boolean;
  expanded?: boolean;
  subItems?: string[];
}) {
  const statusDot =
    status === 'live'
      ? 'bg-mint-400 shadow-[0_0_8px_rgba(63,214,154,0.5)]'
      : status === 'beta'
        ? 'bg-amber-400'
        : 'bg-fg-faint';

  return (
    <div className="space-y-0.5">
      <button
        type="button"
        className={cn(
          'group relative flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[12.5px] font-medium tracking-[-0.005em] transition-colors duration-150 ease-sleek',
          active
            ? 'nav-active-rail bg-white/[0.04] text-fg-primary'
            : 'text-fg-secondary hover:bg-white/[0.025] hover:text-fg-primary',
        )}
      >
        <span className={cn('flex h-4 w-4 items-center justify-center', active ? 'text-ice-300' : 'text-fg-muted')}>
          {icon}
        </span>
        <span className="flex-1 truncate">{label}</span>
        <span className={cn('h-1.5 w-1.5 rounded-full', statusDot)} />
        <ChevronRight
          size={12}
          className={cn(
            'text-fg-faint transition-transform duration-200',
            expanded && 'rotate-90 text-fg-muted',
          )}
        />
      </button>
      {expanded && subItems ? (
        <div className="ml-5 space-y-px border-l border-line-subtle pl-3">
          {subItems.map((item, idx) => (
            <button
              key={item}
              type="button"
              className={cn(
                'flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[11.5px] tracking-[-0.005em] transition-colors',
                idx === 0
                  ? 'text-fg-primary'
                  : 'text-fg-muted hover:text-fg-secondary',
              )}
            >
              {item}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}