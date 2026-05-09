// ============================================================================
// ToolTrace — collapsed-by-default trace of tool execution
// ----------------------------------------------------------------------------
// Modeled after the legacy `ToolExecutionTrace`, but tighter and
// designed to live INSIDE a research card rather than as a free-floating
// chat artifact.  Default state when the turn is done is collapsed (a
// single summary row); click to expand and see each tool call's
// duration + error if any.
//
// During streaming we show the running steps as a vertical list so the
// user can see progress; we collapse only after the turn completes.
// ============================================================================

import { useState } from 'react';
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  Wrench,
} from 'lucide-react';
import type { AssistantPhase, TraceStep } from '@/types/copilot';
import { cn } from '@/utils/cn';

type Props = {
  steps: TraceStep[];
  phase?: AssistantPhase;
};

export function ToolTrace({ steps, phase }: Props) {
  const [expanded, setExpanded] = useState(false);

  if (steps.length === 0) return null;

  const isDone = phase === 'done' || phase === 'error';
  const showSynthesising = phase === 'synthesising';
  const failedCount = steps.filter((s) => s.status === 'error').length;
  const hasFailures = failedCount > 0;

  // While the turn is mid-flight we always render the running list so
  // the user has a heartbeat.  Once done, we collapse unless the user
  // expands.
  const renderFull = !isDone || expanded;

  return (
    <div className="mx-5 mb-3 rounded-lg border border-line-subtle bg-white/[0.012]">
      <button
        type="button"
        onClick={() => isDone && setExpanded((v) => !v)}
        className={cn(
          'flex w-full items-center gap-2 px-3 py-2 text-left',
          isDone && 'cursor-pointer hover:bg-white/[0.018]',
        )}
      >
        {isDone &&
          (expanded ? (
            <ChevronDown size={11} className="shrink-0 text-fg-faint" />
          ) : (
            <ChevronRight size={11} className="shrink-0 text-fg-faint" />
          ))}
        {hasFailures ? (
          <AlertCircle size={11} className="shrink-0 text-coral-400" />
        ) : isDone ? (
          <Check size={11} className="shrink-0 text-mint-400" />
        ) : (
          <Loader2 size={11} className="shrink-0 animate-spin text-ice-300" />
        )}
        <span className="kicker text-fg-muted">
          {isDone
            ? hasFailures
              ? `${steps.length} TOOL${steps.length > 1 ? 'S' : ''} · ${failedCount} FAILED`
              : `${steps.length} TOOL${steps.length > 1 ? 'S' : ''} EXECUTED`
            : `RUNNING · ${steps.length} TOOL${steps.length > 1 ? 'S' : ''}`}
        </span>
      </button>

      {renderFull && (
        <div className="space-y-1.5 border-t border-line-subtle px-3 py-2.5">
          {steps.map((s) => (
            <Row key={s.id} step={s} />
          ))}
          {showSynthesising && (
            <div className="flex items-center gap-2 py-0.5 pl-px">
              <Loader2 size={11} className="animate-spin text-ice-300" />
              <span className="text-[11px] text-fg-secondary">
                Constructing analysis…
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ step }: { step: TraceStep }) {
  const isError = step.status === 'error';
  return (
    <div className="py-0.5">
      <div className="flex items-center gap-2">
        {step.status === 'running' ? (
          <Loader2 size={11} className="animate-spin text-ice-300" />
        ) : step.status === 'complete' ? (
          <Check size={11} className="text-mint-400" />
        ) : (
          <AlertCircle size={11} className="text-coral-400" />
        )}
        <Wrench size={10} className="shrink-0 text-fg-faint" />
        <span
          className={cn(
            'mono text-[11.5px]',
            step.status === 'running'
              ? 'text-fg-secondary'
              : isError
                ? 'text-coral-400'
                : 'text-fg-muted',
          )}
        >
          {step.label}
        </span>
        {step.durationMs != null &&
          (step.status === 'complete' || step.status === 'error') && (
            <span className="mono ml-auto text-[10.5px] text-fg-faint">
              {step.durationMs} ms
            </span>
          )}
      </div>
      {isError && step.error && (
        <div className="mt-0.5 pl-[38px] text-[11px] leading-snug text-coral-400/85">
          {step.error}
        </div>
      )}
    </div>
  );
}
