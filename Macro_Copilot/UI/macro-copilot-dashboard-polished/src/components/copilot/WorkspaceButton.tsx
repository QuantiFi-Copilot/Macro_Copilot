import { ArrowUpRight, BarChart3 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { WorkspaceContext } from '@/types/copilot';

type WorkspaceButtonProps = {
  context: WorkspaceContext;
};

// Human-readable tool labels for the workspace button subtitle.
// Existing chartable tools route to typed views; the analytical models
// route to the rich ModelWorkspacePage (see lib/workspaceContext.ts).
const TOOL_WORKSPACE_LABELS: Record<string, string> = {
  calculate_curve_spread_tool: 'Spread chart & history',
  calculate_cross_market_spread_tool: 'Cross-market chart',
  calculate_butterfly_tool: 'Butterfly decomposition',
  scan_extremes_tool: 'Scanner results & heatmap',
  // Analytical models — open in the model playground.
  calculate_rolling_regression_tool: 'Rolling regression playground',
  calculate_pca_yield_curve_tool: 'PCA loadings, variance, factor scores',
  calculate_yield_change_attribution_pca_tool: 'PCA-based attribution decomposition',
  calculate_half_life_tool: 'Mean-reversion half-life diagnostics',
  calculate_beta_adjusted_spread_tool: 'Beta-adjusted spread playground',
};

export function WorkspaceButton({ context }: WorkspaceButtonProps) {
  const navigate = useNavigate();
  const toolNames = context.tools.map((t) => t.tool);
  const subtitle = toolNames
    .map((t) => TOOL_WORKSPACE_LABELS[t] ?? t)
    .join(' · ');

  const handleClick = () => {
    // SPA navigation — preserves the chat drawer state and lets the
    // WorkspacePage rewrite ?context=... into ?tool=... URL params.
    const encoded = encodeURIComponent(JSON.stringify(context));
    navigate(`/workspace?context=${encoded}`);
  };

  return (
    <button
      onClick={handleClick}
      className="group mt-3 flex w-full items-center gap-3 rounded-lg border border-ice-400/20 bg-ice-500/[0.06] px-3.5 py-2.5 text-left transition-all duration-150 ease-sleek hover:border-ice-400/35 hover:bg-ice-500/[0.1]"
    >
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-ice-400/25 bg-ice-500/15">
        <BarChart3 size={13} className="text-ice-300" />
      </div>
      <div className="min-w-0 flex-1">
        <span className="text-[12px] font-semibold text-ice-200">
          See more in workspace
        </span>
        <span className="mt-0.5 block truncate text-[10px] text-ice-300/60">
          {subtitle}
        </span>
      </div>
      <ArrowUpRight
        size={13}
        className="shrink-0 text-ice-300/40 transition-colors group-hover:text-ice-300"
      />
    </button>
  );
}
