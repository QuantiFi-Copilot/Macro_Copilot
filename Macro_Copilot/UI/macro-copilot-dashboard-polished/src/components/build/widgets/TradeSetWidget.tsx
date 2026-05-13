// ============================================================================
// TradeSetWidget — renderer for TradeSet artifacts.
// ----------------------------------------------------------------------------
// TradeSets are produced by ``construct_trades`` / ``evaluate_trades``
// in the backtest archetype.  Backtest is currently paused from the
// LLM-facing surface (see ``rates_agent/workflows/mcp_server.py``),
// but the artifact type is registered in the substrate and any
// directly-invoked backtest workflow persists with TradeSet rows in
// its DAG.
//
// V1 render: per-trade summary count + reminder that the backtest
// archetype is paused.  PR B will add a real trade-table view when
// the archetype comes back.
// ============================================================================

import type { NodeRenderer } from '@/components/build/lib/nodeRendererRegistry';
import { registerArtifactRenderer } from '@/components/build/lib/nodeRendererRegistry';

const TradeSetWidget: NodeRenderer = ({ artifact }) => {
  return (
    <div className="flex min-h-0 flex-1 flex-col px-5 py-4">
      <div className="text-[10.5px] uppercase tracking-[0.16em] text-fg-muted">
        TradeSet
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="font-serif-display text-[26px] font-light leading-none text-fg-primary">
          {artifact.row_count ?? '—'}
        </span>
        <span className="text-[11px] text-fg-muted">
          {artifact.row_count === 1 ? 'trade' : 'trades'}
        </span>
      </div>
      <p className="mt-3 text-[11px] leading-[1.5] text-fg-secondary">
        Backtest output rendering is paused while data prerequisites
        (DV01, OTR, true O/N OIS, TIPS carry) are in flight.  Trade-
        table detail view ships in PR B.
      </p>
    </div>
  );
};

registerArtifactRenderer('TradeSet', TradeSetWidget);
export { TradeSetWidget };
