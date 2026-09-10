// ============================================================================
// AgentStrip — top-of-page agent picker
// ----------------------------------------------------------------------------
// Six rounded pill buttons.  Active agent gets a soft ice gradient
// fill + mint live-dot.  Dimmed agents (non-rates) show their planned
// scope and a "soon" badge — clickable but disabled (no V1 backend
// data for them).
//
// Counts come from the manifest (live agents only).  Dimmed agent
// counts are shown as "—" since their manifests don't exist yet.
// ============================================================================

import {
  Activity,
  Bot,
  Gauge,
  Layers,
  LineChart,
  ShieldCheck,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';

type AgentDef = {
  /** Manifest key.  When the agent is live, this matches a key in
   *  `manifest.agents`. */
  id: string;
  label: string;
  icon: ReactNode;
  status: 'live' | 'soon';
};

const AGENTS: AgentDef[] = [
  { id: 'rates_agent',  label: 'Rates Agent',  icon: <Layers size={12} />,      status: 'live' },
  { id: 'fx_agent',     label: 'FX Agent',     icon: <LineChart size={12} />,   status: 'live' },
  { id: 'credit_agent', label: 'Credit Agent', icon: <Activity size={12} />,    status: 'soon' },
  { id: 'macro_equity', label: 'Macro Equity', icon: <Gauge size={12} />,       status: 'soon' },
  { id: 'policy_agent', label: 'Policy / Events', icon: <ShieldCheck size={12} />, status: 'soon' },
  { id: 'pm_orch',      label: 'PM Orchestrator',  icon: <Bot size={12} />,        status: 'soon' },
];

type Props = {
  activeAgentId: string;
  /** Map agent id → tool count.  Only live agents will have entries. */
  toolCounts: Record<string, number>;
  onSelect: (agentId: string) => void;
};

export function AgentStrip({ activeAgentId, toolCounts, onSelect }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-8 py-3.5">
      {AGENTS.map((a) => {
        const isLive = a.status === 'live';
        const isActive = a.id === activeAgentId;
        const count = toolCounts[a.id];
        return (
          <button
            key={a.id}
            type="button"
            disabled={!isLive}
            onClick={() => isLive && onSelect(a.id)}
            title={!isLive ? `${a.label} ships in a follow-up` : undefined}
            className={cn(
              'group relative flex h-8 items-center gap-2 rounded-full px-3.5 text-[12px] font-medium tracking-[-0.005em] transition-all duration-200 ease-sleek',
              isActive
                ? 'bg-[linear-gradient(180deg,rgba(122,162,255,0.18),rgba(122,162,255,0.06))] text-ice-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.08),inset_0_0_0_1px_rgba(122,162,255,0.32),0_0_14px_-2px_rgba(122,162,255,0.30)]'
                : isLive
                  ? 'text-fg-secondary ring-1 ring-line-soft hover:bg-white/[0.025] hover:text-fg-primary'
                  : 'cursor-not-allowed text-fg-faint ring-1 ring-line-subtle',
            )}
          >
            <span
              className={cn(
                'flex h-3 w-3 items-center justify-center transition-colors',
                isActive ? 'text-ice-200' : isLive ? 'text-fg-muted' : 'text-fg-faint',
              )}
            >
              {a.icon}
            </span>
            <span>{a.label}</span>
            {isLive ? (
              <>
                {count !== undefined && (
                  <span className="font-mono text-[10px] font-medium text-fg-muted">
                    · {count}
                  </span>
                )}
                <span className="h-1.5 w-1.5 rounded-full bg-mint-400 shadow-[0_0_6px_rgba(63,214,154,0.55)]" />
              </>
            ) : (
              <>
                <span className="h-1.5 w-1.5 rounded-full bg-fg-faint" />
                <span className="font-mono text-[8.5px] font-medium uppercase tracking-[0.16em] text-fg-faint">
                  soon
                </span>
              </>
            )}
          </button>
        );
      })}
    </div>
  );
}
