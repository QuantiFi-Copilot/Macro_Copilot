"""shared.operators.summarize_trades — P&L Panel → summary Panel.

Phase 1 PR 12.  Step 3 (terminal) of the backtest archetype's chain.
Walks the per-trade P&L Panel from ``evaluate_trades`` and emits a
single-row Panel whose columns are summary metrics:

  - ``hit_rate``                 fraction of trades with final P&L >= 0
  - ``mean_pnl``                 mean of per-trade final P&L
  - ``sharpe_annualized``        Sharpe ratio of trade returns
                                 (annualised by sqrt(252 / holding_window))
  - ``max_drawdown``             max drawdown of cumulative trade P&L
  - ``p10_pnl`` / ``p50_pnl`` / ``p90_pnl``  distribution percentiles

The output IS the BacktestReport terminal artifact for the V1
backtest workflow (PR 14 wires this into the template).
``ScalarMetric`` as a typed artifact is deferred to Phase 2 — the
brief's PR-12 closed-family extension is TradeSet; layering a
second closed-family addition into the same PR risks the hash-
stability gate.  Panel-with-one-row stays in the existing closed
family.
"""

from pathlib import Path

from shared.operators.summarize_trades.operator import summarize_trades
from shared.operators.summarize_trades.schemas import SummarizeTradesParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "summarize_trades",
    "SummarizeTradesParams",
    "CONFIG_PATH",
]
