// ============================================================================
// OutputCanvas — dispatches a primitive's run output to the right renderer
// ----------------------------------------------------------------------------
// Looks up the model registry's `outputRenderer` hint, picks the matching
// component, and renders.  Falls back to AutoRenderer when no hint is
// registered or the hint is `auto` — so newly registered primitives still
// produce a sensible canvas with zero per-primitive frontend work.
//
// Renders four states:
//   - empty:    no run yet → guidance card
//   - error:    network or `{ok:false}` envelope → red callout
//   - running:  spinner placeholder
//   - ready:    dispatched output renderer
// ============================================================================

import { AlertCircle, AlertTriangle, Loader2, Sigma } from 'lucide-react';
import { getModelMetadata, type OutputRendererKind } from '@/lib/modelRegistry';
import type { ToolCard, PrimitiveRunResult } from '@/types/workflows';
import { PcaLoadingsRenderer } from './renderers/PcaLoadingsRenderer';
import { RollingRegressionRenderer } from './renderers/RollingRegressionRenderer';
import { AttributionRenderer } from './renderers/AttributionRenderer';
import { AutoRenderer } from './renderers/AutoRenderer';

/** Detect a controlled-error envelope.  Some primitives return
 *  ``{"error": "..."}`` when the math couldn't run (no data, singular
 *  design, small window).  The /tools/{name}/run endpoint forwards that
 *  as ``ok:true`` because Python didn't raise — so we have to sniff. */
function detectControlledError(output: Record<string, unknown>): string | null {
  if (typeof output['error'] === 'string') return output['error'];
  return null;
}

/** Detect "ran but produced no usable metrics".  Most rates primitives
 *  surface a ``current_metrics`` snapshot; if it's missing or every
 *  numeric field on it is null, the run produced empty output and the
 *  user deserves an explanatory banner instead of em-dashes. */
function detectEmptyMetrics(output: Record<string, unknown>): boolean {
  const metrics = output['current_metrics'] as Record<string, unknown> | undefined;
  if (!metrics) return false; // Primitive may not surface current_metrics at all.
  let hasFinite = false;
  for (const v of Object.values(metrics)) {
    if (typeof v === 'number' && Number.isFinite(v)) {
      hasFinite = true;
      break;
    }
  }
  return !hasFinite;
}

export function OutputCanvas({
  card,
  result,
  isRunning,
  errorMessage,
}: {
  card: ToolCard;
  result: PrimitiveRunResult | null;
  isRunning: boolean;
  errorMessage: string | null;
}) {
  if (errorMessage) {
    return <ErrorCard title="Network error" message={errorMessage} />;
  }
  if (result && !result.ok) {
    return <ErrorCard title="Primitive returned an error" message={result.error} />;
  }
  if (isRunning) {
    return <RunningCard tool={card.tool_name} />;
  }
  if (!result) {
    return <EmptyCard card={card} />;
  }

  // Sniff the output dict for the primitive's controlled-error shape
  // (`{"error": "…"}`) — surface it as a clean error card rather than
  // letting the renderer paint em-dashes.
  const controlledError = detectControlledError(result.output);
  if (controlledError) {
    return (
      <ErrorCard
        title="The primitive could not produce a result"
        message={controlledError}
      />
    );
  }

  const renderer = getModelMetadata(card.tool_name)?.outputRenderer ?? 'auto';
  const emptyMetrics = detectEmptyMetrics(result.output);

  return (
    <div className="space-y-3">
      {emptyMetrics ? (
        <DiagnosticBanner toolName={card.tool_name} />
      ) : null}
      <RenderDispatch renderer={renderer} output={result.output} />
    </div>
  );
}

function RenderDispatch({
  renderer,
  output,
}: {
  renderer: OutputRendererKind;
  output: Record<string, unknown>;
}) {
  switch (renderer) {
    case 'pca':
      return <PcaLoadingsRenderer output={output} />;
    case 'rolling_regression':
      return <RollingRegressionRenderer output={output} />;
    case 'attribution':
      return <AttributionRenderer output={output} />;
    case 'time_series':
    case 'series_panel':
    case 'auto':
    default:
      return <AutoRenderer output={output} />;
  }
}

// ---------------------------------------------------------------------------

function EmptyCard({ card }: { card: ToolCard }) {
  return (
    <div className="flex h-full min-h-[360px] flex-col items-center justify-center text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-line-soft bg-white/[0.02]">
        <Sigma size={16} className="text-ice-300" />
      </div>
      <h3 className="mt-3 text-[13px] font-semibold text-fg-primary">
        Configure inputs and run
      </h3>
      <p className="mt-1 max-w-md text-[11.5px] leading-[1.55] text-fg-secondary">
        {card.methodology.what_it_does}
      </p>
    </div>
  );
}

function RunningCard({ tool }: { tool: string }) {
  return (
    <div className="flex h-full min-h-[360px] flex-col items-center justify-center text-center">
      <Loader2 size={20} className="animate-spin text-ice-300" />
      <p className="mt-3 mono text-[11.5px] text-fg-muted">{tool}</p>
      <p className="mt-1 text-[11px] text-fg-faint">running…</p>
    </div>
  );
}

function ErrorCard({ title, message }: { title: string; message: string }) {
  return (
    <div className="card flex items-start gap-3 border-coral-400/30 px-4 py-4">
      <AlertCircle size={14} className="mt-0.5 shrink-0 text-coral-300" />
      <div className="min-w-0">
        <div className="text-[12.5px] font-semibold text-coral-200">{title}</div>
        <div className="mt-1 text-[11.5px] leading-[1.55] text-fg-secondary">
          {message}
        </div>
      </div>
    </div>
  );
}

function DiagnosticBanner({ toolName }: { toolName: string }) {
  return (
    <div className="card flex items-start gap-3 border-amber-400/30 bg-amber-400/5 px-4 py-3">
      <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-300" />
      <div className="min-w-0">
        <div className="text-[12px] font-semibold text-amber-200">
          Run completed but produced no usable metrics
        </div>
        <div className="mt-1 text-[11px] leading-[1.55] text-fg-secondary">
          <code className="mono text-amber-300">{toolName}</code> returned a
          response, but every snapshot scalar (R², alpha, betas, …) came back
          null. Common causes:
          <span className="mt-1.5 block">• the chosen series has no overlapping data on the picked window</span>
          <span>• the rolling window is shorter than the primitive's <code className="mono text-ice-300">min_periods</code> threshold</span>
          <span>• the design matrix was near-singular (collinear regressors or single-row windows)</span>
          <span className="mt-1.5 block">
            See <span className="text-fg-secondary">Lineage · this run</span> on the right rail for the
            exact params + resolved conventions.
          </span>
        </div>
      </div>
    </div>
  );
}
