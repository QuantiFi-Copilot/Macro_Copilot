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

import { AlertCircle, Loader2, Sigma } from 'lucide-react';
import { getModelMetadata, type OutputRendererKind } from '@/lib/modelRegistry';
import type { ToolCard, PrimitiveRunResult } from '@/types/workflows';
import { PcaLoadingsRenderer } from './renderers/PcaLoadingsRenderer';
import { RollingRegressionRenderer } from './renderers/RollingRegressionRenderer';
import { AttributionRenderer } from './renderers/AttributionRenderer';
import { AutoRenderer } from './renderers/AutoRenderer';

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

  const renderer = getModelMetadata(card.tool_name)?.outputRenderer ?? 'auto';
  return <RenderDispatch renderer={renderer} output={result.output} />;
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
