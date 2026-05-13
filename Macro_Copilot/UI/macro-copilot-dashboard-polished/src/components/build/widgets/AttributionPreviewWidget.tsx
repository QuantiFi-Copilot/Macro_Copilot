// ============================================================================
// AttributionPreviewWidget — per-tool renderer for yield-change attribution.
// ----------------------------------------------------------------------------
// Registered under ``(Series, calculate_yield_change_attribution_pca_tool)``
// AND ``(Panel, calculate_yield_change_attribution_pca_tool)`` because
// the attribution primitive can emit either shape depending on its
// configured output_field (per-component series vs row-by-component
// panel summary).
//
// V1 surfaces the bound configuration (target tenor + window) so the
// user can read "what's being attributed across what window" from
// the card alone.  The full per-component decomposition (level /
// slope / curvature contributions in bps) lives in the artifact
// payload; the rich bar-chart view ships in the follow-up renderer
// that fetches the payload lazily.
// ============================================================================

import { useMemo } from 'react';
import { Scale } from 'lucide-react';
import type {
  NodeRenderer,
  NodeRenderProps,
} from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { ArtifactSparkline } from './shared/ArtifactSparkline';
import { ArtifactStatsBlock } from './shared/ArtifactStatsBlock';

const AttributionPreviewWidget: NodeRenderer = ({
  artifact,
  category,
  node,
  size,
}) => {
  const meta = useAttributionMeta(node);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-2 px-5 pt-3 pb-1.5">
        <div className="flex min-w-0 items-center gap-2">
          <Scale
            size={12}
            strokeWidth={1.75}
            className="shrink-0 text-violet-300"
          />
          <span className="truncate text-[11px] font-medium text-fg-secondary">
            {meta.title}
          </span>
        </div>
        {meta.windowLabel && (
          <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-faint">
            {meta.windowLabel}
          </span>
        )}
      </div>
      {artifact.artifact_type === 'Series' ? (
        <>
          <ArtifactSparkline
            artifact={artifact}
            category={category}
            height={size === 'small' ? 56 : 92}
          />
          <ArtifactStatsBlock artifact={artifact} compact={size === 'small'} />
        </>
      ) : (
        <PanelSummaryFallback artifact={artifact} />
      )}
      {meta.components.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 border-t border-line-subtle px-5 py-2">
          <span className="kicker text-fg-faint">Components</span>
          {meta.components.map((c) => (
            <span key={c} className="dag-pill dag-pill-operator">
              {c}
            </span>
          ))}
        </div>
      )}
    </div>
  );
};

function PanelSummaryFallback({
  artifact,
}: {
  artifact: NodeRenderProps['artifact'];
}) {
  return (
    <div className="flex flex-1 items-baseline gap-2 px-5 py-4">
      <span className="font-serif-display text-[24px] font-light leading-none text-fg-primary">
        {artifact.row_count ?? '—'}
      </span>
      <span className="text-[11px] text-fg-muted">
        {artifact.row_count === 1 ? 'component' : 'components'} attributed
      </span>
    </div>
  );
}

function useAttributionMeta(node: NodeRenderProps['node']) {
  return useMemo(() => {
    const inner = readInnerParams(node.params);
    const target =
      readString(inner, 'target_tenor') ??
      readString(inner, 'target') ??
      null;
    const components =
      readStringArray(inner, 'factors') ??
      readStringArray(inner, 'components') ??
      ['Level', 'Slope', 'Curvature'];
    const window =
      readNumber(inner, 'window_days') ?? readNumber(inner, 'lookback') ?? null;
    const title = target
      ? `Attribution · ${String(target).toUpperCase()}`
      : 'Yield-change attribution';
    return {
      title,
      components,
      windowLabel: window ? `${window}d window` : null,
    };
  }, [node]);
}

function readInnerParams(
  params: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  if (!params || typeof params !== 'object') return {};
  const inner = (params as Record<string, unknown>)['params'];
  if (inner && typeof inner === 'object') {
    return inner as Record<string, unknown>;
  }
  return params as Record<string, unknown>;
}

function readString(
  obj: Record<string, unknown> | null | undefined,
  key: string,
): string | null {
  if (!obj) return null;
  const v = obj[key];
  return typeof v === 'string' && v.length > 0 ? v : null;
}

function readNumber(
  obj: Record<string, unknown> | null | undefined,
  key: string,
): number | null {
  if (!obj) return null;
  const v = obj[key];
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  return null;
}

function readStringArray(
  obj: Record<string, unknown> | null | undefined,
  key: string,
): string[] | null {
  if (!obj) return null;
  const v = obj[key];
  if (!Array.isArray(v)) return null;
  const out: string[] = [];
  for (const item of v) {
    if (typeof item === 'string' && item.length > 0) out.push(item);
  }
  return out.length > 0 ? out : null;
}

registerToolRenderer(
  {
    artifactType: 'Series',
    toolName: 'calculate_yield_change_attribution_pca_tool',
  },
  AttributionPreviewWidget,
);
registerToolRenderer(
  {
    artifactType: 'Panel',
    toolName: 'calculate_yield_change_attribution_pca_tool',
  },
  AttributionPreviewWidget,
);

export { AttributionPreviewWidget };
