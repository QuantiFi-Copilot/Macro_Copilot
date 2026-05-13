// ============================================================================
// EventStudyDashboard — specialised Results dashboard for event_study.
// ----------------------------------------------------------------------------
// PR7 — organises the persisted event_study artifacts into a
// canonical analysis page:
//
//   ┌── Setup ─────────────────────────────────────────────────────┐
//   │  signal · target  (two NodeWidgetCards)                       │
//   ├── Events ─────────────────────────────────────────────────────┤
//   │  EventSet card — real event count from PR4 widget             │
//   ├── Event windows ──────────────────────────────────────────────┤
//   │  WindowedPanel card — offsets + per-event table from PR4      │
//   ├── Conditional vs unconditional aggregate ────────────────────┤
//   │  conditional + unconditional Series cards side-by-side        │
//   ├── Abnormal response (terminal) ──────────────────────────────┤
//   │  ``compare`` Series card — the canonical event-study output   │
//   └───────────────────────────────────────────────────────────────┘
//
// Composition rule: every section either renders the corresponding
// node's existing NodeWidgetCard (PR4 payload-backed) OR a
// ``MissingArtifactCard`` when the resolver returned ``null`` for
// that role.  No fabricated content; honest fallbacks only.
//
// Sources of truth
// ----------------
// - Role mapping comes from ``resolveEventStudyArtifacts``.
// - Widget rendering reuses ``NodeWidgetCard`` from PR4 + ``StageCard``
//   already in use elsewhere on Build.
// - Slot params displayed at the top come from ``workspace.bound_slot_values``
//   so the user sees the actual threshold / window / curve / tenor
//   that produced the events.
// ============================================================================

import type { WorkspaceDetail } from '@/services/workspaceApi';
import { NodeWidgetCard } from '../NodeWidgetCard';
import { DashboardSection } from '../lib/DashboardSection';
import { MissingArtifactCard } from '../lib/MissingArtifactCard';
import {
  resolveEventStudyArtifacts,
  type EventStudyRoles,
  type WorkflowArtifactMap,
} from '../lib/resolveWorkflowArtifacts';
import { GenericResultsDashboard } from './GenericResultsDashboard';
import { SlotSummaryStrip } from '../lib/SlotSummaryStrip';

type Props = {
  detail: WorkspaceDetail;
};

export function EventStudyDashboard({ detail }: Props) {
  const resolved = resolveEventStudyArtifacts(detail);
  return (
    <div className="flex min-w-0 flex-col gap-7 px-6 py-6">
      <DashboardSection
        label="Event study"
        description="Signal → events → forward windows → conditional vs unconditional aggregate.  Sections below render the real persisted artifacts; missing pieces are called out honestly."
      >
        <SlotSummaryStrip
          detail={detail}
          highlightSlots={[
            'signal_tool_name',
            'signal_output_field',
            'target_tool_name',
            'threshold',
            'post_window',
          ]}
        />
      </DashboardSection>

      <SetupSection detail={detail} resolved={resolved} />
      <EventsSection detail={detail} resolved={resolved} />
      <WindowsSection detail={detail} resolved={resolved} />
      <AggregateSection detail={detail} resolved={resolved} />
      <OutputSection detail={detail} resolved={resolved} />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Setup — signal + target primitives
// ----------------------------------------------------------------------------

function SetupSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<EventStudyRoles>;
}) {
  const { signal, target } = resolved.roles;
  return (
    <DashboardSection
      label="Setup"
      description="The signal series whose extremes define events, and the target series whose response is measured around each event."
      count={[signal, target].filter(Boolean).length}
      countLabel="primitives"
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {signal ? (
          <NodeWidgetCard node={signal} workspace={detail} size="medium" />
        ) : (
          <MissingArtifactCard roleLabel="signal primitive" />
        )}
        {target ? (
          <NodeWidgetCard node={target} workspace={detail} size="medium" />
        ) : (
          <MissingArtifactCard roleLabel="target primitive" />
        )}
      </div>
    </DashboardSection>
  );
}

// ----------------------------------------------------------------------------
// Events — EventSet payload-backed widget (counts come from PR4 widget)
// ----------------------------------------------------------------------------

function EventsSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<EventStudyRoles>;
}) {
  const { events } = resolved.roles;
  return (
    <DashboardSection
      label="Events"
      description="Days where |signal z-score| crossed the threshold.  Count + first/last/preview dates come from the persisted EventSet payload."
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {events ? (
          <NodeWidgetCard node={events} workspace={detail} size="wide" />
        ) : (
          <MissingArtifactCard
            roleLabel="EventSet"
            reason="No threshold_events stage persisted an EventSet for this workspace."
            spanClass="col-span-12"
          />
        )}
      </div>
    </DashboardSection>
  );
}

// ----------------------------------------------------------------------------
// Event windows — WindowedPanel payload
// ----------------------------------------------------------------------------

function WindowsSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<EventStudyRoles>;
}) {
  const { windows } = resolved.roles;
  return (
    <DashboardSection
      label="Event windows"
      description="Per-event slice of the target series around each event date, with the offset axis labelled t−N … t+N."
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {windows ? (
          <NodeWidgetCard node={windows} workspace={detail} size="wide" />
        ) : (
          <MissingArtifactCard
            roleLabel="WindowedPanel"
            reason="No event_windows stage persisted a WindowedPanel for this workspace."
            spanClass="col-span-12"
          />
        )}
      </div>
    </DashboardSection>
  );
}

// ----------------------------------------------------------------------------
// Aggregate — conditional + unconditional Series cards side by side
// ----------------------------------------------------------------------------

function AggregateSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<EventStudyRoles>;
}) {
  const { conditionalAggregate, unconditionalAggregate } = resolved.roles;
  // If neither aggregate is present, skip the section entirely —
  // the abnormal-response output section will still surface the
  // terminal artifact.
  if (!conditionalAggregate && !unconditionalAggregate) {
    return null;
  }
  return (
    <DashboardSection
      label="Conditional vs unconditional aggregate"
      description="The conditional path averages the target's t0…t+N moves on event days only.  The unconditional path averages every day — the no-information baseline."
      count={
        [conditionalAggregate, unconditionalAggregate].filter(Boolean).length
      }
      countLabel="series"
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {conditionalAggregate ? (
          <NodeWidgetCard
            node={conditionalAggregate}
            workspace={detail}
            size="medium"
          />
        ) : (
          <MissingArtifactCard
            roleLabel="conditional aggregate"
            reason="The conditional_aggregate stage on the event branch didn't persist a Series."
          />
        )}
        {unconditionalAggregate ? (
          <NodeWidgetCard
            node={unconditionalAggregate}
            workspace={detail}
            size="medium"
          />
        ) : (
          <MissingArtifactCard
            roleLabel="unconditional aggregate"
            reason="The unconditional_aggregate baseline branch didn't persist a Series."
          />
        )}
      </div>
    </DashboardSection>
  );
}

// ----------------------------------------------------------------------------
// Output — terminal `compare` Series (the abnormal-response answer)
// ----------------------------------------------------------------------------

function OutputSection({
  detail,
  resolved,
}: {
  detail: WorkspaceDetail;
  resolved: WorkflowArtifactMap<EventStudyRoles>;
}) {
  const { output } = resolved.roles;
  return (
    <DashboardSection
      label="Abnormal response"
      description="Conditional minus unconditional — the per-offset signed delta that answers “is the post-event move different from a random day.”"
    >
      <div className="grid grid-cols-12 gap-4 lg:gap-5">
        {output ? (
          <NodeWidgetCard node={output} workspace={detail} size="wide" />
        ) : (
          <MissingArtifactCard
            roleLabel="abnormal-response series"
            reason="The terminal compare stage didn't persist a Series.  Without it the workflow's canonical answer isn't available."
            spanClass="col-span-12"
          />
        )}
      </div>
    </DashboardSection>
  );
}

// ----------------------------------------------------------------------------
// "All artifacts" fall-through — re-exported as a named helper so the
// Results tab can offer a generic-grid toggle below the specialised
// surface.
// ----------------------------------------------------------------------------

export function EventStudyAllArtifactsFallback({
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
