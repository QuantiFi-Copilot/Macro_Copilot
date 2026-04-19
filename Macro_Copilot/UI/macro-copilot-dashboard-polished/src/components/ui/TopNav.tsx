import { NavLink } from 'react-router-dom';
import { Bell, Bookmark, Radio, Search, SlidersHorizontal } from 'lucide-react';
import { cn } from '@/utils/cn';

type TabDef = {
  label: string;
  path: string;
};

type TopNavProps = {
  tabs: TabDef[];
};

const MARKET_TICKERS = [
  { label: 'US 10Y', value: '4.287', delta: '+3.2', dir: 'up' as const },
  { label: 'DXY', value: '104.86', delta: '-0.18', dir: 'down' as const },
  { label: 'SPX', value: '5,127', delta: '+0.42%', dir: 'up' as const },
  { label: 'WTI', value: '78.44', delta: '-1.1%', dir: 'down' as const },
];

export function TopNav({ tabs }: TopNavProps) {
  const focusCopilotComposer = () => {
    window.dispatchEvent(new Event('copilot:focus-input'));
  };

  return (
    <header className="relative flex flex-col">
      {/* Top row: tabs + search + utilities */}
      <div className="flex h-[64px] items-center gap-6 border-b border-line-subtle px-6">
        <nav className="flex items-center">
          {tabs.map((tab) => (
            <NavLink
              key={tab.path}
              to={tab.path}
              end={tab.path === '/'}
              className={({ isActive }) =>
                cn(
                  'relative -mb-px flex h-[64px] items-center px-3 text-[13px] font-medium tracking-[-0.005em] transition-colors duration-150 ease-sleek',
                  isActive
                    ? 'text-fg-primary'
                    : 'text-fg-muted hover:text-fg-secondary',
                )
              }
            >
              {({ isActive }) => (
                <>
                  {tab.label}
                  {isActive ? (
                    <span className="absolute inset-x-3 bottom-0 h-[2px] rounded-t-full bg-gradient-to-r from-transparent via-ice-400 to-transparent shadow-[0_0_12px_rgba(122,162,255,0.45)]" />
                  ) : null}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Global search — command-k style */}
        <div className="flex flex-1 justify-center">
          <button
            type="button"
            onClick={focusCopilotComposer}
            className="flex w-full max-w-[520px] items-center gap-2.5 rounded-lg border border-line-soft bg-white/[0.015] px-3.5 py-2 text-[12.5px] text-fg-muted transition-colors duration-150 ease-sleek hover:border-line-strong hover:bg-white/[0.03] hover:text-fg-secondary"
          >
            <Search size={13} />
            <span className="flex-1 text-left">
              Ask the copilot, or jump to an instrument…
            </span>
            <kbd className="hidden rounded border border-line-soft bg-white/[0.025] px-1.5 py-[1px] text-[9px] font-medium text-fg-muted lg:inline-flex">
              ⌘K
            </kbd>
          </button>
        </div>

        <div className="flex items-center gap-1.5">
          <button className="btn-ghost h-9 px-3">
            <Bookmark size={13} />
            <span className="hidden xl:inline">Save View</span>
          </button>
          <button className="btn-ghost h-9 w-9 !px-0">
            <SlidersHorizontal size={13} />
          </button>
          <button className="btn-ghost relative h-9 w-9 !px-0">
            <Bell size={13} />
            <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-amber-400 shadow-[0_0_8px_rgba(243,183,85,0.6)]" />
          </button>
        </div>
      </div>

      {/* Status strip: live tickers + session clock */}
      <div className="flex h-[38px] items-center gap-6 border-b border-line-subtle bg-white/[0.008] px-6">
        <div className="flex items-center gap-1.5 text-fg-secondary">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-mint-400 opacity-50" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-mint-400" />
          </span>
          <Radio size={11} className="text-fg-muted" />
          <span className="text-[10.5px] font-semibold uppercase tracking-[0.14em]">
            Live · NY Open
          </span>
        </div>

        <div className="h-3 w-px bg-line-soft" />

        <div className="flex min-w-0 flex-1 items-center gap-6 overflow-hidden">
          {MARKET_TICKERS.map((t) => (
            <div key={t.label} className="flex shrink-0 items-center gap-2 text-[11px]">
              <span className="font-semibold uppercase tracking-[0.12em] text-fg-muted">
                {t.label}
              </span>
              <span className="mono font-medium text-fg-primary">{t.value}</span>
              <span
                className={cn(
                  'mono text-[10.5px] font-medium',
                  t.dir === 'up' ? 'text-mint-400' : 'text-coral-400',
                )}
              >
                {t.delta}
              </span>
            </div>
          ))}
        </div>

        <div className="flex shrink-0 items-center gap-3 text-[11px] text-fg-muted">
          <span className="mono">
            {new Date().toLocaleTimeString('en-US', {
              hour: '2-digit',
              minute: '2-digit',
              hour12: false,
            })}{' '}
            EST
          </span>
          <span className="h-3 w-px bg-line-soft" />
          <span className="font-medium uppercase tracking-[0.14em]">Session · RTH</span>
        </div>
      </div>
    </header>
  );
}
