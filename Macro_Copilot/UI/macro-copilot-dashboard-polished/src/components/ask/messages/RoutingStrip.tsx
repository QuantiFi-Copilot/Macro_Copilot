// ============================================================================
// RoutingStrip — top of every assistant research card
// ----------------------------------------------------------------------------
// Surfaces the *route* the orchestrator picked for this turn so the user
// can see at a glance: did we run a registered workflow template, an
// ad-hoc DAG, or fall through to the supervisor's tool-calling path?
//
// Three modes:
//   - TEMPLATE · <template_id>      → workflow router won the turn
//   - COMPOSED · <N> nodes          → ad-hoc operator DAG (V2)
//   - PRIMITIVES · <N> tools        → supervisor + N tool calls
//   - THINKING                      → no decision yet (mid-stream)
//
// Right side: an "as of" date — pulled from the message's timestamp
// today; V2 will surface the data freshness as_of_date the substrate
// already records on every artifact.
// ============================================================================

import { GitFork, Sparkles, Workflow } from 'lucide-react';
import type { CopilotMessage } from '@/types/copilot';
import { cn } from '@/utils/cn';

export function RoutingStrip({ message }: { message: CopilotMessage }) {
  const route = describeRoute(message);
  return (
    <div className="flex items-center justify-between gap-3 px-5 pt-3.5 pb-3">
      <div className="flex min-w-0 items-center gap-2.5">
        <span
          className={cn(
            'flex h-6 w-6 shrink-0 items-center justify-center rounded-md',
            route.kind === 'template' &&
              'border border-ice-400/30 bg-ice-500/10 text-ice-300',
            route.kind === 'composed' &&
              'border border-lineage-400/30 bg-lineage-500/10 text-lineage-300',
            route.kind === 'primitives' &&
              'border border-line-soft bg-white/[0.02] text-fg-secondary',
            route.kind === 'thinking' &&
              'border border-line-soft bg-white/[0.015] text-fg-muted',
          )}
        >
          {route.kind === 'template' ? (
            <Workflow size={11} />
          ) : route.kind === 'composed' ? (
            <GitFork size={11} />
          ) : (
            <Sparkles size={11} />
          )}
        </span>
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="kicker text-fg-muted">{route.kicker}</span>
          {route.detail && (
            <span className="mono truncate text-[12px] font-medium text-fg-primary">
              {route.detail}
            </span>
          )}
        </div>
      </div>
      <span className="mono shrink-0 text-[10.5px] text-fg-faint">
        {formatAsOf(message.timestamp)}
      </span>
    </div>
  );
}

function describeRoute(message: CopilotMessage): {
  kind: 'template' | 'composed' | 'primitives' | 'thinking';
  kicker: string;
  detail?: string;
} {
  if (message.workflow?.routeDecision.template_id) {
    return {
      kind: 'template',
      kicker: 'TEMPLATE',
      detail: message.workflow.routeDecision.template_id,
    };
  }
  // V2: detect ad-hoc operator DAG by sniffing message metadata.
  // Today the supervisor never produces this shape, so we never enter
  // this branch — left in the union for forward-compatibility.

  const toolCount = message.traceSteps.filter(
    (s) => s.status === 'complete' || s.status === 'error',
  ).length;
  if (toolCount > 0) {
    return {
      kind: 'primitives',
      kicker: toolCount === 1 ? 'PRIMITIVE' : `PRIMITIVES · ${toolCount}`,
    };
  }

  if (message.phase === 'thinking' || message.phase === 'running_tools') {
    return { kind: 'thinking', kicker: 'ROUTING' };
  }
  return { kind: 'thinking', kicker: 'COPILOT' };
}

function formatAsOf(d: Date): string {
  // "as of <YYYY-MM-DD HH:MM> UTC" — short ISO with explicit zone so
  // PMs reading across timezones know the freshness anchor.
  const iso = d.toISOString();
  const date = iso.slice(0, 10);
  const time = iso.slice(11, 16);
  return `as of ${date} · ${time} UTC`;
}
