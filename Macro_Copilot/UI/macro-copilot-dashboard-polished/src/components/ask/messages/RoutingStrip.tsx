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
    <div className="flex items-center justify-between gap-3 px-5 pt-4 pb-3">
      <div className="flex min-w-0 items-center gap-2.5">
        <span
          className={cn(
            'flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-[6px] transition-all',
            // Soft gradient + inset ring instead of a hard border —
            // pairs visually with the gradient top-rule on the card.
            route.kind === 'template' &&
              'bg-[linear-gradient(180deg,rgba(122,162,255,0.18),rgba(122,162,255,0.06))] text-ice-200 shadow-[inset_0_1px_0_rgba(255,255,255,0.08),inset_0_0_0_1px_rgba(122,162,255,0.28)]',
            route.kind === 'composed' &&
              'bg-[linear-gradient(180deg,rgba(155,140,255,0.18),rgba(155,140,255,0.06))] text-lineage-200 shadow-[inset_0_1px_0_rgba(255,255,255,0.08),inset_0_0_0_1px_rgba(155,140,255,0.28)]',
            route.kind === 'primitives' &&
              'bg-[linear-gradient(180deg,rgba(155,140,255,0.10),rgba(155,140,255,0.03))] text-lineage-300 shadow-[inset_0_1px_0_rgba(255,255,255,0.05),inset_0_0_0_1px_rgba(155,140,255,0.16)]',
            route.kind === 'thinking' &&
              'bg-white/[0.02] text-fg-muted shadow-[inset_0_0_0_1px_rgba(148,163,184,0.10)]',
          )}
        >
          {route.kind === 'template' ? (
            <Workflow size={11} strokeWidth={2} />
          ) : route.kind === 'composed' ? (
            <GitFork size={11} strokeWidth={2} />
          ) : (
            <Sparkles size={11} strokeWidth={2} />
          )}
        </span>
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="text-[10px] font-medium uppercase tracking-[0.16em] text-fg-muted">
            {route.kicker}
          </span>
          {route.detail && (
            <span className="font-mono truncate text-[12.5px] font-medium tracking-[-0.005em] text-fg-primary">
              {route.detail}
            </span>
          )}
        </div>
      </div>
      <span className="font-mono shrink-0 text-[10.5px] tracking-[0.02em] text-fg-faint">
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
