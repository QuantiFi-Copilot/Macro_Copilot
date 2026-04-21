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

type ToolExecutionTraceProps = {
  steps: TraceStep[];
  phase?: AssistantPhase;
  totalDurationMs?: number;
};

export function ToolExecutionTrace({
  steps,
  phase,
  totalDurationMs,
}: ToolExecutionTraceProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  if (steps.length === 0) return null;

  const isDone = phase === 'done' || phase === 'error';
  const showSynthesising = phase === 'synthesising';

  const failedCount = steps.filter((s) => s.status === 'error').length;
  const hasFailures = failedCount > 0;
  const summaryLabel = hasFailures
    ? `${steps.length} tool call${steps.length > 1 ? 's' : ''}, ${failedCount} failed`
    : `${steps.length} deterministic tool call${steps.length > 1 ? 's' : ''}`;

  const StatusIcon = () =>
    hasFailures ? (
      <AlertCircle size={11} className="shrink-0 text-coral-400" />
    ) : (
      <Check size={11} className="shrink-0 text-mint-400" />
    );

  if (isDone && !isExpanded) {
    return (
      <button
        type="button"
        onClick={() => setIsExpanded(true)}
        className="mb-2.5 flex w-full items-center gap-2 rounded-lg border border-line-soft bg-white/[0.018] px-3 py-2 text-left text-[10.5px] text-fg-muted transition-colors duration-150 ease-sleek hover:border-line-strong hover:bg-white/[0.03] hover:text-fg-secondary"
      >
        <ChevronRight size={12} className="shrink-0 text-fg-faint" />
        <StatusIcon />
        <span className="mono">{summaryLabel}</span>
        {totalDurationMs != null && (
          <>
            <span className="text-fg-faint">·</span>
            <span className="mono">{(totalDurationMs / 1000).toFixed(1)}s total</span>
          </>
        )}
      </button>
    );
  }

  return (
    <div className="mb-3 rounded-lg border border-line-soft bg-white/[0.015] px-3 py-2.5">
      {isDone && (
        <button
          type="button"
          onClick={() => setIsExpanded(false)}
          className="mb-2 flex w-full items-center gap-2 text-left text-[10.5px] text-fg-muted transition-colors duration-150 ease-sleek hover:text-fg-secondary"
        >
          <ChevronDown size={12} className="shrink-0 text-fg-faint" />
          <StatusIcon />
          <span className="mono">{summaryLabel}</span>
          {totalDurationMs != null && (
            <>
              <span className="text-fg-faint">·</span>
              <span className="mono">{(totalDurationMs / 1000).toFixed(1)}s total</span>
            </>
          )}
        </button>
      )}

      <div className="space-y-1.5">
        {steps.map((step) => (
          <TraceStepRow key={step.id} step={step} />
        ))}

        {showSynthesising && (
          <div className="flex items-center gap-2 py-0.5">
            <Loader2 size={11} className="animate-spin text-ice-300" />
            <span className="text-[11px] text-fg-secondary">
              Constructing analysis...
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function TraceStepRow({ step }: { step: TraceStep }) {
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
            'text-[11px] transition-colors duration-150',
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
            <span className="mono ml-auto text-[10px] text-fg-faint">
              {step.durationMs}ms
            </span>
          )}
      </div>

      {isError && step.error && (
        // Muted coral error detail under the tool label. Indented to
        // visually group with its parent step.
        <div className="mt-0.5 pl-[38px] text-[10.5px] leading-snug text-coral-400/90">
          {step.error}
        </div>
      )}
    </div>
  );
}
