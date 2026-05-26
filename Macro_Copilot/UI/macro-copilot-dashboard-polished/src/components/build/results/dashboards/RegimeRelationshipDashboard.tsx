// ============================================================================
// RegimeRelationshipDashboard — specialised Results dashboard for
// regime_conditioned_relationship.
// ----------------------------------------------------------------------------
// PR7 — organises the persisted regime-relationship artifacts into a
// canonical analysis page:
//
//   ┌── Inputs ──────────────────────────────────────────────────┐
//   │  lhs · rhs · regime_signal  (three NodeWidgetCards)         │
//   ├── Regime construction ─────────────────────────────────────┤
//   │  high_mask + low_mask EventSet cards                        │
//   ├── Relationship model ──────────────────────────────────────┤
//   │  relationship + beta cards                                  │
//   ├── Conditional summaries ───────────────────────────────────┤
//   │  high_summary + low_summary Panel cards side-by-side        │
//   ├── Comparison output (terminal) ────────────────────────────┤
//   │  compare card (high vs low delta)                           │
//   └─────────────────────────────────────────────────────────────┘
//
// Composition rule: same as ``EventStudyDashboard`` — every section
// renders the resolved node's payload-backed widget OR a
// ``MissingArtifactCard``.  No fabrication; honest fallbacks only.
// ============================================================================

import type { WorkspaceDetail } from '@/services/workspaceApi';
import { NodeWidgetCard } from '../NodeWidgetCard';
import { DashboardSection } from '../lib/DashboardSection';
import { MissingArtifactCard } from '../lib/MissingArtifactCard';
import { SlotSummaryStrip } from '../lib/SlotSummaryStrip';
import {
  resolveRegimeArtifacts,
  type RegimeRoles,
  type WorkflowArtifactMap,
} from '../lib/resolveWorkflowArtifacts';
import { GenericResultsDashboard } from './GenericResultsDashboard';

type Props = {
  detail: WorkspaceDetail;
};

export function RegimeRelationshipDashboard({ detail }: Props) {
  const resolved = resolveRegimeArtifacts(detail);
  return (
    <div className="flex min-w-0 flex-col gap-7 px-6 py-6">
      <DashboardSection
        label="Regime-conditioned relationship"
        description="Two series feed a rolling relationship model; a third regime signal partitions the timeline into high / low regimes.  Each regime gets its own summary."
      >
        <SlotSummaryStrip
          detail={detail}
          highlightSlots={[
            'lhs_tool_name',
            'rhs_tool_name',
            'regime_signal_tool_name',
            'regression_window_days',
            'regime_threshold',
          ]}
        />
      </DashboardSection>

      <InputsSection detail={detail} resolved={resolved} />
      <RegimeConstructionSection detail={detail} resolved={resolved} />
      <RelationshipSection detail={detail} resolved={resolved} />
      <ConditionalSummariesSection detail={detail} resolved={resolved} />
      <OutputSection detail={detail} resolved={resolved} />
    </div>
  );
}

function InputsSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<RegimeRoles>;
}) {
  const { lhs, rhs, regimeSignal } = resolved.roles;
  return (
    <DashboardSection
      label="Inputs"
      description="The two primitive series whose relationship is modelled, and the third regime signal that partitions the timeline."
      count={[lhs, rhs, regimeSignal].filter(Boolean).length}
      countLabel="primitives"
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {lhs ? (
          <NodeWidgetCard node={lhs} workspace={detail} size="medium" />
        ) : (
          <MissingArtifactCard
            roleLabel="LHS (dependent) primitive"
            spanClass="col-span-12 md:col-span-6 lg:col-span-4"
          />
        )}
        {rhs ? (
          <NodeWidgetCard node={rhs} workspace={detail} size="medium" />
        ) : (
          <MissingArtifactCard
            roleLabel="RHS (explanatory) primitive"
            spanClass="col-span-12 md:col-span-6 lg:col-span-4"
          />
        )}
        {regimeSignal ? (
          <NodeWidgetCard
            node={regimeSignal}
            workspace={detail}
            size="medium"
          />
        ) : (
          <MissingArtifactCard
            roleLabel="regime signal primitive"
            spanClass="col-span-12 md:col-span-6 lg:col-span-4"
          />
        )}
      </div>
    </DashboardSection>
  );
}

function RegimeConstructionSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<RegimeRoles>;
}) {
  const { highMask, lowMask } = resolved.roles;
  if (!highMask && !lowMask) return null;
  return (
    <DashboardSection
      label="Regime construction"
      description="Boolean masks identifying days in each regime.  Counts come from the persisted EventSet payloads."
      count={[highMask, lowMask].filter(Boolean).length}
      countLabel="masks"
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {highMask ? (
          <NodeWidgetCard node={highMask} workspace={detail} size="medium" />
        ) : (
          <MissingArtifactCard roleLabel="high-regime mask" />
        )}
        {lowMask ? (
          <NodeWidgetCard node={lowMask} workspace={detail} size="medium" />
        ) : (
          <MissingArtifactCard roleLabel="low-regime mask" />
        )}
      </div>
    </DashboardSection>
  );
}

function RelationshipSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<RegimeRoles>;
}) {
  const { relationship, beta } = resolved.roles;
  return (
    <DashboardSection
      label="Relationship model"
      description="Rolling regression of LHS on RHS — exposed both as the full coefficient SeriesSet and the selected beta time series."
      count={[relationship, beta].filter(Boolean).length}
      countLabel="artifacts"
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {relationship ? (
          <NodeWidgetCard
            node={relationship}
            workspace={detail}
            size="medium"
          />
        ) : (
          <MissingArtifactCard roleLabel="rolling regression SeriesSet" />
        )}
        {beta ? (
          <NodeWidgetCard node={beta} workspace={detail} size="medium" />
        ) : (
          <MissingArtifactCard roleLabel="selected beta Series" />
        )}
      </div>
    </DashboardSection>
  );
}

function ConditionalSummariesSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<RegimeRoles>;
}) {
  const { highSummary, lowSummary } = resolved.roles;
  if (!highSummary && !lowSummary) return null;
  return (
    <DashboardSection
      label="Conditional summaries"
      description="Per-regime aggregate over the selected beta time series — mean / std / sample size etc. — with the high regime on the left and the low regime on the right."
      count={[highSummary, lowSummary].filter(Boolean).length}
      countLabel="panels"
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {highSummary ? (
          <NodeWidgetCard
            node={highSummary}
            workspace={detail}
            size="medium"
          />
        ) : (
          <MissingArtifactCard roleLabel="high-regime summary panel" />
        )}
        {lowSummary ? (
          <NodeWidgetCard
            node={lowSummary}
            workspace={detail}
            size="medium"
          />
        ) : (
          <MissingArtifactCard roleLabel="low-regime summary panel" />
        )}
      </div>
    </DashboardSection>
  );
}

function OutputSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<RegimeRoles>;
}) {
  const { output } = resolved.roles;
  return (
    <DashboardSection
      label="Comparison output"
      description="High-regime minus low-regime — the canonical regime-conditioned answer."
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {output ? (
          <NodeWidgetCard node={output} workspace={detail} size="wide" />
        ) : (
          <MissingArtifactCard
            roleLabel="comparison output"
            reason="The terminal compare stage didn't persist an artifact."
            spanClass="col-span-12"
          />
        )}
      </div>
    </DashboardSection>
  );
}

export function RegimeAllArtifactsFallback({
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
