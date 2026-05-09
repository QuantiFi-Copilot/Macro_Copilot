// ============================================================================
// AgentPlaceholderPage — honest "coming in V2" page for non-rates agents
// ----------------------------------------------------------------------------
// FX, Credit, Macro Equity, Policy/Events, PM Orchestrator all route to
// this same component with different metadata.  Each surface declares
// what's coming, what data it'll source, and a soft CTA back to /ask
// or /monitor so the user has a productive next move.
//
// When real data lands for an agent, swap this for a RatesAgentPage-
// like surface — the widget engine already handles everything else.
// ============================================================================

import { Link } from 'react-router-dom';
import { ArrowRight, Sparkles, Layers } from 'lucide-react';
import type { ReactNode } from 'react';

type Props = {
  /** Display name (e.g. "FX Agent"). */
  agentName: string;
  /** Headline accent rendered in serif italic.  E.g. "soon" or "next". */
  accent: string;
  /** What this agent will eventually do. */
  description: string;
  /** A short list of things this agent will surface — kept honest about
   *  scope, not promising anything that isn't on the roadmap. */
  scope: string[];
  /** Optional icon for the page header. */
  icon?: ReactNode;
};

export function AgentPlaceholderPage({
  agentName,
  accent,
  description,
  scope,
  icon,
}: Props) {
  return (
    <div className="flex h-full w-full items-center justify-center px-6">
      <div className="relative max-w-[560px] text-left">
        {/* Soft ambient orb behind the headline — same primitive used
            on the Ask empty state. */}
        <div
          aria-hidden
          className="ambient-orb"
          style={{
            left: 'calc(50% - 320px)',
            top: 'calc(50% - 320px)',
          }}
        />

        <div className="relative z-10">
          <div className="flex h-9 w-9 items-center justify-center rounded-md border border-line-soft bg-white/[0.018] text-fg-muted">
            {icon ?? <Layers size={14} />}
          </div>
          <p className="mt-5 font-mono text-[10.5px] font-medium uppercase tracking-[0.16em] text-fg-muted">
            AGENT · {agentName.toUpperCase()}
          </p>
          <h1 className="mt-3 text-[30px] font-light leading-[1.1] tracking-[-0.018em] text-fg-primary">
            Coming{' '}
            <span className="font-serif-display text-[1.05em] font-normal italic text-ice-100">
              {accent}
            </span>
            .
          </h1>
          <p className="mt-3 text-[14px] leading-[1.6] text-fg-secondary">
            {description}
          </p>

          {scope.length > 0 && (
            <ul className="mt-6 space-y-2">
              {scope.map((line) => (
                <li
                  key={line}
                  className="flex items-baseline gap-3 text-[12.5px] leading-[1.55] text-fg-secondary"
                >
                  <Sparkles
                    size={11}
                    className="mt-0.5 shrink-0 text-lineage-300/70"
                  />
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-8 flex items-center gap-4">
            <Link
              to="/"
              className="inline-flex items-center gap-1.5 text-[13px] text-ice-300 transition-colors hover:text-ice-200 hover:underline hover:underline-offset-4"
            >
              Back to Monitor
              <ArrowRight size={12} />
            </Link>
            <span className="font-mono text-[10.5px] text-fg-faint">·</span>
            <Link
              to="/ask"
              className="inline-flex items-center gap-1.5 text-[13px] text-fg-secondary transition-colors hover:text-fg-primary"
            >
              Ask the copilot
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Per-agent presets — exported so AppShell can pick the right copy
// without duplicating it.

import { Activity, Bot, Gauge, LineChart, ShieldCheck } from 'lucide-react';

export function FxAgentPlaceholder() {
  return (
    <AgentPlaceholderPage
      agentName="FX"
      accent="next"
      icon={<LineChart size={14} />}
      description="Spot, forwards, NDFs, vol surfaces, carry, and CIP deviations across G10 and EM. The same widget engine that powers Rates will host FX-deep defaults the day data lands."
      scope={[
        'Spot levels with rolling z-scores across G10 + EM majors',
        'Forward points and implied carry over standard tenors',
        'Vol surfaces and VRP across at-the-money + skew',
        'CIP deviations and cross-currency basis monitors',
      ]}
    />
  );
}

export function CreditAgentPlaceholder() {
  return (
    <AgentPlaceholderPage
      agentName="Credit"
      accent="next"
      icon={<Activity size={14} />}
      description="IG / HY spreads, CDX / iTraxx indices, single-name CDS, and term structure of credit risk. Pairs with the Rates agent for swap-spread / asset-swap RV."
      scope={[
        'IG / HY index levels and rolling z-scores',
        'Single-name CDS spreads with liquidity tier tagging',
        'Term-structure decomposition (5s10s credit curve)',
        'Cross-asset relationship widgets (rates ↔ credit β)',
      ]}
    />
  );
}

export function MacroEquityPlaceholder() {
  return (
    <AgentPlaceholderPage
      agentName="Macro Equity"
      accent="later"
      icon={<Gauge size={14} />}
      description="Index levels, sector returns, factor exposures, implied vol surfaces, dispersion. Macro-flavored equity — not single-name fundamentals."
      scope={[
        'Index level and forward-PE widgets across DM + EM',
        'Sector dispersion and factor exposures (Fama-French + macro)',
        'VIX / SKEW / VRP monitors with regime overlays',
        'Cross-asset relationship widgets (rates ↔ equity β)',
      ]}
    />
  );
}

export function PolicyEventsPlaceholder() {
  return (
    <AgentPlaceholderPage
      agentName="Policy / Events"
      accent="next"
      icon={<ShieldCheck size={14} />}
      description="Central-bank meeting calendars with WIRP-implied paths, hawkishness/dovishness scoring of policy speeches, and macro release surprise tracking."
      scope={[
        'WIRP meeting-by-meeting curves with our deviation flags',
        'Central-bank speech NLP scores (hawk / dove drift)',
        'Macro release calendar with surprise z-scores',
        'Pre / post-event window analytics integrated with Build',
      ]}
    />
  );
}

export function PmOrchestratorPlaceholder() {
  return (
    <AgentPlaceholderPage
      agentName="PM Orchestrator"
      accent="later"
      icon={<Bot size={14} />}
      description="Cross-agent orchestration — composing rates × FX × credit answers into a single synthesis. The Ask surface uses an early version of this; the standalone agent surface ships once the supporting agents land."
      scope={[
        'Multi-agent routing with synthesized answers',
        'Cross-asset working set and provenance unification',
        'Saved playbooks (e.g. "morning macro brief")',
        'Long-running research threads with replayable lineage',
      ]}
    />
  );
}
