// ============================================================================
// PcaPreviewWidget — per-tool renderer for PCA primitives.
// ----------------------------------------------------------------------------
// Registered under ``(Series, calculate_pca_yield_curve_tool)``.  The
// PCA primitive emits a Series-shaped artifact (the projected factor
// time series), which the generic SeriesWidget would render
// adequately — but the workspace-summary preview hides the factor
// vocabulary, so the user has no way to tell PC1 from PC3 at a glance.
//
// This renderer reads the node's params (``output_field`` carries the
// factor index, e.g. "factor_1") and the bound curve / tenors to
// produce a card that names the factor + the curve it lives on.  The
// full PCA loadings heatmap + variance-explained breakdown lives in
// the artifact payload and ships in a follow-up renderer that fetches
// the payload lazily.
// ============================================================================

import { useMemo } from 'react';
import { Layers } from 'lucide-react';
import type {
  NodeRenderer,
  NodeRenderProps,
} from '@/components/build/lib/nodeRendererRegistry';
import { registerToolRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { ArtifactSparkline } from './shared/ArtifactSparkline';
import { ArtifactStatsBlock } from './shared/ArtifactStatsBlock';

const PcaPreviewWidget: NodeRenderer = ({ artifact, category, node, size }) => {
  const meta = usePcaMeta(node);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-2 px-5 pt-3 pb-1.5">
        <div className="flex min-w-0 items-center gap-2">
          <Layers
            size={12}
            strokeWidth={1.75}
            className="shrink-0 text-violet-300"
          />
          <span className="truncate text-[11px] font-medium text-fg-secondary">
            {meta.factorLabel}
          </span>
        </div>
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-faint">
          {meta.curveLabel}
        </span>
      </div>
      <ArtifactSparkline
        artifact={artifact}
        category={category}
        height={size === 'small' ? 56 : 92}
      />
      <ArtifactStatsBlock artifact={artifact} compact={size === 'small'} />
      <PcaFooter tenors={meta.tenors} />
    </div>
  );
};

function PcaFooter({ tenors }: { tenors: string[] | null }) {
  if (!tenors || tenors.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1 border-t border-line-subtle px-5 py-2">
      <span className="kicker text-fg-faint">Tenor basis</span>
      {tenors.slice(0, 8).map((t) => (
        <span key={t} className="dag-pill dag-pill-operator">
          {t}
        </span>
      ))}
      {tenors.length > 8 && (
        <span className="font-mono text-[10px] text-fg-faint">
          +{tenors.length - 8}
        </span>
      )}
    </div>
  );
}

function usePcaMeta(node: NodeRenderProps['node']) {
  return useMemo(() => {
    const inner = readInnerParams(node.params);
    const outputField = readString(inner, 'output_field')
      ?? readString(node.params, 'output_field')
      ?? null;
    const factorLabel = formatFactorLabel(outputField);
    const curve =
      readString(inner, 'curve') ??
      readString(inner, 'curve_family') ??
      readString(inner, 'instrument') ??
      null;
    const tenors = readStringArray(inner, 'tenors');
    return {
      factorLabel,
      curveLabel: curve ? curve.toUpperCase() : 'PCA',
      tenors,
    };
  }, [node]);
}

function formatFactorLabel(outputField: string | null): string {
  if (!outputField) return 'PCA factor';
  const m = outputField.match(/factor[_\-\s]*(\d+)/i);
  if (m) return `Factor ${m[1]}`;
  if (/level/i.test(outputField)) return 'Level factor';
  if (/slope/i.test(outputField)) return 'Slope factor';
  if (/curv(ature)?/i.test(outputField)) return 'Curvature factor';
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

// Register under the PCA-projecting tool.  The attribution tool
// (calculate_yield_change_attribution_pca_tool) also emits Series-
// shaped output but reads more naturally through the AttributionPreview
// renderer — see AttributionPreviewWidget.
registerToolRenderer(
  { artifactType: 'Series', toolName: 'calculate_pca_yield_curve_tool' },
  PcaPreviewWidget,
);

export { PcaPreviewWidget };
