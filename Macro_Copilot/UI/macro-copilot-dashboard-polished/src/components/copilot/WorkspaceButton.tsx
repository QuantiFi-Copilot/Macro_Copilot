import { ArrowUpRight, BarChart3 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { WorkspaceContext } from '@/types/copilot';

type WorkspaceButtonProps = {
  context: WorkspaceContext;
};

// Human-readable tool labels for the workspace button subtitle
const TOOL_WORKSPACE_LABELS: Record<string, string> = {
  calculate_curve_spread_tool: 'Spread chart & history',
  calculate_cross_market_spread_tool: 'Cross-market chart',
  calculate_butterfly_tool: 'Butterfly decomposition',
  scan_extremes_tool: 'Scanner results & heatmap',
  get_fx_spot_level_tool: 'FX spot snapshot',
  get_fx_carry_tool: 'FX carry ranking',
  get_fx_forward_curve_tool: 'FX forward curve',
  scan_fx_extremes_tool: 'FX scanner',
  get_fx_realized_vol_tool: 'FX realized volatility',
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
