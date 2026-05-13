// ============================================================================
// RollingRegressionPreviewWidget — per-tool renderer for rolling-regression.
// ----------------------------------------------------------------------------
// Registered under ``(Series, calculate_rolling_regression_tool)``.
//
// The rolling-regression primitive emits a Series-shaped artifact — by
// convention the rolling beta (or rolling alpha / R²) of a target
// against one or more regressors.  The generic SeriesWidget would
// render the line correctly, but lose the user's mental model of
// "what's regressed on what" — so this renderer surfaces the
// target / regressor pair + the window size in the card kicker.
//
// The full coefficient stack (multi-regressor betas, R², residuals)
// lives in the artifact payload; that view ships in the follow-up
// PR that adds an artifact-payload fetch endpoint.
// ============================================================================

import { useMemo } from 'react';
import { Sigma } from 'lucide-react';
import type {
  NodeRenderer,
  NodeRenderProps,
} from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { ArtifactSparkline } from './shared/ArtifactSparkline';
import { ArtifactStatsBlock } from './shared/ArtifactStatsBlock';

const RollingRegressionPreviewWidget: NodeRenderer = ({
  artifact,
  category,
  node,
  size,
}) => {
  const meta = useRollingRegressionMeta(node);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-2 px-5 pt-3 pb-1.5">
        <div className="flex min-w-0 items-center gap-2">
          <Sigma
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
      <ArtifactSparkline
        artifact={artifact}
        category={category}
        height={size === 'small' ? 56 : 92}
      />
      <ArtifactStatsBlock artifact={artifact} compact={size === 'small'} />
      {meta.regressors.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 border-t border-line-subtle px-5 py-2">
          <span className="kicker text-fg-faint">Regressors</span>
          {meta.regressors.slice(0, 5).map((r) => (
            <span key={r} className="dag-pill dag-pill-operator">
              {r}
            </span>
          ))}
          {meta.regressors.length > 5 && (
            <span className="font-mono text-[10px] text-fg-faint">
              +{meta.regressors.length - 5}
            </span>
          )}
        </div>
      )}
    </div>
  );
};

function useRollingRegressionMeta(node: NodeRenderProps['node']) {
  return useMemo(() => {
    const inner = readInnerParams(node.params);
    const outputField = readString(inner, 'output_field')
      ?? readString(node.params, 'output_field')
      ?? null;
    const metric = formatMetricLabel(outputField);
    const target = readString(inner, 'target') ?? readString(inner, 'y') ?? null;
    const regressors =
      readStringArray(inner, 'regressors') ??
      readStringArray(inner, 'x') ??
      [];
    const window =
      readNumber(inner, 'window_days') ?? readNumber(inner, 'window') ?? null;
    const title = composeTitle(metric, target, regressors);
    return {
      title,
      regressors,
      windowLabel: window ? `${window}d window` : null,
    };
  }, [node]);
}

function composeTitle(
  metric: string,
  target: string | null,
  regressors: string[],
): string {
  if (target && regressors.length > 0) {
    return `${metric} · ${target} ~ ${regressors.slice(0, 2).join(' + ')}${
      regressors.length > 2 ? ' …' : ''
    }`;
  }
  if (target) return `${metric} · ${target}`;
  return metric;
}

function formatMetricLabel(outputField: string | null): string {
  if (!outputField) return 'Rolling regression';
  const lower = outputField.toLowerCase();
  if (lower.includes('beta')) return 'Rolling β';
  if (lower.includes('alpha')) return 'Rolling α';
  if (lower.includes('r_squared') || lower.includes('rsquared'))
    return 'Rolling R²';
  if (lower.includes('residual')) return 'Residual';
  return outputField;
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
  { artifactType: 'Series', toolName: 'calculate_rolling_regression_tool' },
  RollingRegressionPreviewWidget,
);

export { RollingRegressionPreviewWidget };
