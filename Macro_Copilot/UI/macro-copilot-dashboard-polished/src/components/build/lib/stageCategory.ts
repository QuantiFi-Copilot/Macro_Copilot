// ============================================================================
// stageCategory.ts — classify a workspace node into a visual category.
// ----------------------------------------------------------------------------
// The substrate persists ``NodeSummary.kind`` as ``"primitive"`` or
// ``"operator"``.  The Build UI surfaces that as a three-way visual
// category so the DAG view + result cards can color-code consistently
// with Monitor's `data` / `analysis` / `anomaly` rail palette.
//
// Single rule:
//   - terminal node                → ``output``  (amber rail)
//   - kind === "primitive"         → ``input``   (ice rail)
//   - kind === "operator"          → ``transform`` (violet rail)
//   - anything else                → ``transform`` fallback
//
// The CSS rail color is sourced via ``railColorForStage``; the
// numbered-badge color is sourced via ``badgeColorForStage``.  Both
// keep colors out of component code so a future palette swap is a
// one-file change.
// ============================================================================

import type { StageCategory } from './buildTypes';
import type { NodeSummary, WorkspaceDetail } from '@/services/workspaceApi';

/**
 * Decide which visual category a node belongs to within a workspace.
 *
 * ``terminalNodeId`` defaults to ``workspace.focus_node`` because
 * persisted workspaces stamp the terminal node id there (see
 * ``state.workspace_repo.create_workspace``).  Pass an explicit
 * override when previewing a draft DAG before persistence.
 */
export function stageCategoryForNode(
  node: NodeSummary,
  workspace: Pick<WorkspaceDetail, 'focus_node'>,
  /** Optional override when the persisted focus_node doesn't match
   *  the active selection in the UI. */
  terminalNodeIdOverride?: string | null,
): StageCategory {
  const terminalId =
    terminalNodeIdOverride ?? workspace.focus_node ?? null;
  if (terminalId && node.node_id === terminalId) return 'output';
  if (node.kind === 'primitive') return 'input';
  if (node.kind === 'operator') return 'transform';
  return 'transform';
}

/** CSS rail color (matches Monitor's WidgetCard `--rail-color`).
 *  Returns an ``rgba(...)`` literal so callers can pipe it directly
 *  into a style prop without intermediate translation. */
export function railColorForStage(category: StageCategory): string {
  switch (category) {
    case 'input':
      return 'rgba(122, 162, 255, 0.55)'; // ice
    case 'transform':
      return 'rgba(155, 140, 255, 0.45)'; // violet
    case 'output':
      return 'rgba(243, 183, 85, 0.55)'; // amber
  }
}

/** Tailwind class set for the numbered badge on each stage card.
 *  Returns three classes (border, background, text) so the
 *  consumer can compose with ``cn(...)``. */
export function badgeClassesForStage(category: StageCategory): {
  border: string;
  bg: string;
  text: string;
} {
  switch (category) {
    case 'input':
      return {
        border: 'border-ice-400/40',
        bg: 'bg-ice-500/15',
        text: 'text-ice-200',
      };
    case 'transform':
      return {
        border: 'border-violet-400/40',
        bg: 'bg-violet-500/15',
        text: 'text-violet-200',
      };
    case 'output':
      return {
        border: 'border-amber-400/40',
        bg: 'bg-amber-500/15',
        text: 'text-amber-200',
      };
  }
}

/** Reads the substrate's `WidgetCategory` (data / analysis / anomaly)
 *  for a given stage so we can pass it straight to ``WidgetCard``.
 *  Mapping mirrors the rail palette above. */
export function widgetCategoryForStage(
  category: StageCategory,
): 'data' | 'analysis' | 'anomaly' {
  switch (category) {
    case 'input':
      return 'data';
    case 'transform':
      return 'analysis';
    case 'output':
      return 'anomaly';
  }
}
