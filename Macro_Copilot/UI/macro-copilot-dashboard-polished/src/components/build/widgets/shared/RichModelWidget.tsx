// ============================================================================
// RichModelWidget — bridge from a Build node to a bespoke model renderer.
// ----------------------------------------------------------------------------
// Phase R3.  Persisted workspaces emit artifact summaries with sparse
// preview arrays — fine for Series sparklines but not nearly enough for
// the rich PCA / RollingRegression / Attribution UIs the user has on the
// model-builder surface.  The substrate exposes a tool-run endpoint
// (``POST /tools/{name}/run``) that returns the full output dict given
// the same params the node already carries; the cleanest frontend-only
// bridge is to re-run the tool when the widget mounts and feed the
// result into the bespoke renderer.
//
// Once the backend ships a ``GET /artifacts/{hash}/payload`` endpoint
// (phase R5 follow-up), this hook gets a lighter-weight path that
// fetches the cached payload by hash instead of re-running.  Until then,
// re-run keeps the same lineage chain — same params, same code, same
// answer.
//
// Each per-tool widget (PcaWidget, RollingRegressionWidget,
// AttributionWidget) is a thin shell that picks a renderer and hands
// over to this component.
// ============================================================================

import { useEffect, useState, type ComponentType } from 'react';
import { AlertCircle, Loader2 } from 'lucide-react';
import { runPrimitive } from '@/services/workflowsApi';
import type { PrimitiveRunResult } from '@/types/workflows';
import type { NodeRenderProps } from '@/components/build/lib/nodeRendererRegistry';

/** Renderer contract — every bespoke model renderer accepts the raw
 *  output dict the tool emits.  Matches the legacy
 *  ``model-workspace/renderers/*`` signature so the renderers can be
 *  lifted verbatim without rewrapping. */
export type RichModelRenderer = ComponentType<{
  output: Record<string, unknown>;
}>;

type Props = NodeRenderProps & {
  /** Bespoke renderer (PcaLoadingsRenderer / RollingRegressionRenderer
   *  / AttributionRenderer) — the visual unit that turns the output
   *  dict into the rich chart suite. */
  Renderer: RichModelRenderer;
  /** Display name surfaced on loading / error states.  Defaults to the
   *  tool name extracted from the node. */
  toolDisplayName?: string;
};

export function RichModelWidget({ node, Renderer, toolDisplayName }: Props) {
  const { toolName, innerParams } = extractToolCall(node);
  const [output, setOutput] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  useEffect(() => {
    if (!toolName) {
      setError('Node is missing a tool_name in its params.');
      return;
    }
    let cancelled = false;
    setIsRunning(true);
    setError(null);
    setOutput(null);
    runPrimitive(toolName, innerParams)
      .then((res: PrimitiveRunResult) => {
        if (cancelled) return;
        if (res.ok) {
          setOutput(res.output);
        } else {
          setError(res.error);
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setIsRunning(false);
      });
    return () => {
      cancelled = true;
    };
  }, [toolName, JSON.stringify(innerParams)]); // eslint-disable-line react-hooks/exhaustive-deps

  if (isRunning) {
    return <RunningState toolName={toolDisplayName ?? toolName ?? 'tool'} />;
  }
  if (error) {
    return <ErrorState message={error} toolName={toolDisplayName ?? toolName ?? 'tool'} />;
  }
  if (!output) return null;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
      <Renderer output={output} />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

function extractToolCall(node: NodeRenderProps['node']): {
  toolName: string | null;
  innerParams: Record<string, unknown>;
} {
  const params = (node.params ?? {}) as Record<string, unknown>;
  const toolName =
    typeof params['tool_name'] === 'string'
      ? (params['tool_name'] as string)
      : null;
  // The substrate stores PrimitiveNode params as
  // ``{tool_name, output_field, params: <inner>}``.  Unwrap the
  // inner dict so ``runPrimitive`` sees what the tool's input
  // schema expects.
  const inner =
    typeof params['params'] === 'object' && params['params'] !== null
      ? (params['params'] as Record<string, unknown>)
      : params;
  return { toolName, innerParams: inner };
}

function RunningState({ toolName }: { toolName: string }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-5 py-8 text-fg-muted">
      <Loader2 size={16} className="animate-spin text-ice-300" />
      <span className="text-[11.5px]">Re-running {toolName} for full output…</span>
    </div>
  );
}

function ErrorState({ message, toolName }: { message: string; toolName: string }) {
  return (
    <div className="flex min-h-0 flex-1 items-start gap-2 px-5 py-4">
      <AlertCircle size={14} className="mt-0.5 shrink-0 text-coral-300" />
      <div className="min-w-0">
        <div className="text-[11.5px] font-semibold text-fg-primary">
          Couldn't load rich output for {toolName}
        </div>
        <div className="mt-1 text-[10.5px] leading-[1.5] text-fg-secondary">
          {message}
        </div>
        <div className="mt-2 text-[10px] text-fg-faint">
          The artifact's preview values are still readable; open the Parameters
          tab to inspect the node's full configuration.
        </div>
      </div>
    </div>
  );
}
