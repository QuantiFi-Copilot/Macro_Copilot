// ============================================================================
// BacktestDashboard — specialised Results dashboard for backtest.
// ----------------------------------------------------------------------------
// PR7 — backtest is a real backend template, but its LLM-facing
// invocation surface is currently paused (per
// ``rates_agent/workflows/mcp_server.py`` + PR1's
// ``PAUSED_WORKFLOWS`` registry).  Two cases this dashboard must
// handle honestly:
//
//   1. Workspace has persisted backtest artifacts (someone invoked
//      the workflow directly OR before it was paused).  Render the
//      real artifacts — signal / events / trades / evaluate /
//      summary — with payload-backed widgets from PR4.
//
//   2. Workspace declares ``template_id === 'backtest'`` but has no
//      persisted artifacts for the trade-evaluation stages.  Surface
//      an honest paused banner; render whatever upstream artifacts
//      did persist; mark missing stages explicitly.
//
// In BOTH cases the paused banner appears at the top so users can't
// mistake a workspace that pre-dates the pause for an active
// backtest run.
//
// Discipline (matches Codex's PR7 brief):
//   - No fabricated trades, P&L, Sharpe, or any metric.
//   - No fake equity curves.
//   - Real persisted TradeSet payloads render through the PR4
//     ``TradeSetWidget``; absent payloads render the
//     ``MissingArtifactCard``.
// ============================================================================

import { Construction } from 'lucide-react';
import type { WorkspaceDetail } from '@/services/workspaceApi';
import { NodeWidgetCard } from '../NodeWidgetCard';
import { DashboardSection } from '../lib/DashboardSection';
import { MissingArtifactCard } from '../lib/MissingArtifactCard';
import { SlotSummaryStrip } from '../lib/SlotSummaryStrip';
import {
  resolveBacktestArtifacts,
  type BacktestRoles,
  type WorkflowArtifactMap,
} from '../lib/resolveWorkflowArtifacts';
import { GenericResultsDashboard } from './GenericResultsDashboard';

type Props = {
  detail: WorkspaceDetail;
};

export function BacktestDashboard({ detail }: Props) {
  const resolved = resolveBacktestArtifacts(detail);

  // Did execution actually produce trades / summary?  If those
  // canonical post-execution artifacts are absent we surface a
  // strong "execution unavailable" line even within the always-
  // pinned paused banner.
  const hasPersistedExecution =
    resolved.roles.trades !== null ||
    resolved.roles.evaluate !== null ||
    resolved.roles.summarize !== null;

  return (
    <div className="flex min-w-0 flex-col gap-7 px-6 py-6">
      <PausedBanner hasPersistedExecution={hasPersistedExecution} />

      <DashboardSection
        label="Backtest"
        description="Signal → trade construction → trade evaluation → summary.  The dashboard renders persisted artifacts only; nothing here is computed client-side."
      >
        <SlotSummaryStrip
          detail={detail}
          highlightSlots={[
            'signal_tool_name',
            'signal_params',
            'threshold',
            'sovereign_curves',
            'tenors',
            'start_date',
            'end_date',
            'financing_method',
          ]}
        />
      </DashboardSection>

      <SetupSection detail={detail} resolved={resolved} />
      <TradeConstructionSection detail={detail} resolved={resolved} />
      <DataDependenciesSection detail={detail} resolved={resolved} />
      <EvaluationSection
        detail={detail}
        resolved={resolved}
        hasPersistedExecution={hasPersistedExecution}
      />
      <SummarySection
        detail={detail}
        resolved={resolved}
        hasPersistedExecution={hasPersistedExecution}
      />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Paused banner — always present for backtest dashboards.
// ----------------------------------------------------------------------------

function PausedBanner({
  hasPersistedExecution,
}: {
  hasPersistedExecution: boolean;
}) {
  return (
    <section className="flex items-start gap-3 rounded-lg border border-amber-400/30 bg-amber-500/[0.06] px-4 py-3">
      <Construction
        size={13}
        strokeWidth={1.75}
        aria-hidden
        className="mt-0.5 shrink-0 text-amber-300"
      />
      <div className="min-w-0">
        <div className="text-[12px] font-semibold text-amber-200">
          Backtest archetype · paused on the LLM surface
        </div>
        <p className="mt-1 text-[10.5px] leading-[1.5] text-fg-secondary">
          The backtest workflow is currently gated off the LLM-facing
          MCP surface (data prerequisites — DV01, OTR, true O/N OIS,
          TIPS carry — are still being wired in).  This dashboard
          renders any artifacts the workspace persisted directly;{' '}
          {hasPersistedExecution ? (
            <>
              the trade-evaluation stages below show real persisted
              payloads.
            </>
          ) : (
            <>
              the trade-evaluation stages below show as missing because
              the workflow didn’t run end-to-end.  Nothing is
              fabricated — no trades, P&amp;L, Sharpe, or comparison
              values are computed client-side.
            </>
          )}
        </p>
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------------
// Setup — signal + events
// ----------------------------------------------------------------------------

function SetupSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<BacktestRoles>;
}) {
  const { signal, events } = resolved.roles;
  return (
    <DashboardSection
      label="Setup"
      description="The signal that defines entry events + the resulting EventSet mask.  Both come from persisted primitive / operator artifacts."
      count={[signal, events].filter(Boolean).length}
      countLabel="stages"
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {signal ? (
          <NodeWidgetCard node={signal} workspace={detail} size="medium" />
        ) : (
          <MissingArtifactCard roleLabel="signal primitive" />
        )}
        {events ? (
          <NodeWidgetCard node={events} workspace={detail} size="medium" />
        ) : (
          <MissingArtifactCard roleLabel="entry-event EventSet" />
        )}
      </div>
    </DashboardSection>
  );
}

function TradeConstructionSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<BacktestRoles>;
}) {
  const { trades } = resolved.roles;
  return (
    <DashboardSection
      label="Trade construction"
      description="construct_trades emits a raw TradeSet (entry / exit / legs / weights).  Counts + per-trade preview come from the persisted payload."
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {trades ? (
          <NodeWidgetCard node={trades} workspace={detail} size="wide" />
        ) : (
          <MissingArtifactCard
            roleLabel="TradeSet (construct_trades)"
            reason="No trade-construction stage persisted a TradeSet.  Without it the evaluation + summary sections have nothing to compute against."
            spanClass="col-span-12"
          />
        )}
      </div>
    </DashboardSection>
  );
}

function DataDependenciesSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<BacktestRoles>;
}) {
  const { pricePanel, financing } = resolved.roles;
  if (!pricePanel && !financing) return null;
  return (
    <DashboardSection
      label="Evaluation data"
      description="The price + financing panels evaluate_trades reads to compute P&amp;L.  Surfaced here so a user can audit the input data without leaving the dashboard."
      count={[pricePanel, financing].filter(Boolean).length}
      countLabel="panels"
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {pricePanel ? (
          <NodeWidgetCard
            node={pricePanel}
            workspace={detail}
            size="medium"
          />
        ) : (
          <MissingArtifactCard roleLabel="price panel" />
        )}
        {financing ? (
          <NodeWidgetCard
            node={financing}
            workspace={detail}
            size="medium"
          />
        ) : (
          <MissingArtifactCard roleLabel="financing-rate panel" />
        )}
      </div>
    </DashboardSection>
  );
}

function EvaluationSection({
  detail,
  resolved,
  hasPersistedExecution,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<BacktestRoles>;
  hasPersistedExecution: boolean;
}) {
  const { evaluate } = resolved.roles;
  return (
    <DashboardSection
      label="Trade evaluation"
      description="evaluate_trades enriches each trade with PnL + return.  Rendered from the persisted TradeSet only — no client-side P&amp;L computation."
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {evaluate ? (
          <NodeWidgetCard node={evaluate} workspace={detail} size="wide" />
        ) : (
          <MissingArtifactCard
            roleLabel="evaluated TradeSet"
            reason={
              hasPersistedExecution
                ? 'The evaluate_trades stage didn’t persist this run’s TradeSet.'
                : 'Backtest execution is paused — no evaluated TradeSet was persisted.  Re-run when the archetype resumes.'
            }
            spanClass="col-span-12"
          />
        )}
      </div>
    </DashboardSection>
  );
}

function SummarySection({
  detail,
  resolved,
  hasPersistedExecution,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<BacktestRoles>;
  hasPersistedExecution: boolean;
}) {
  const { summarize } = resolved.roles;
  return (
    <DashboardSection
      label="Summary"
      description="The terminal summarize_trades Panel.  Headline scalar metrics here come directly from the persisted Panel cells; no client-side derivation."
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {summarize ? (
          <NodeWidgetCard node={summarize} workspace={detail} size="wide" />
        ) : (
          <MissingArtifactCard
            roleLabel="trade summary"
            reason={
              hasPersistedExecution
                ? 'The summarize_trades terminal stage didn’t persist a Panel.'
                : 'Backtest execution is paused — no summary Panel was persisted.  The dashboard renders an honest empty state rather than fabricating Sharpe / CAGR / drawdown.'
            }
            spanClass="col-span-12"
          />
        )}
      </div>
    </DashboardSection>
  );
}

export function BacktestAllArtifactsFallback({
  detail,
}: {
  detail: WorkspaceDetail;
}) {
  return (
    <GenericResultsDashboard
      detail={detail}
      showTerminalSection={false}
      intermediateLabel="All artifacts"
      intermediateDescription="Every persisted artifact this workspace produced, including ones not surfaced in the sections above."
    />
  );
}
