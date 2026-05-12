"""shared.operators.evaluate_trades — TradeSet + price Panel → P&L Panel.

Phase 1 PR 12.  Step 2 of the backtest archetype's chain.  Walks
each trade's holding window against the price/yield panel and
emits a wide Panel keyed by ``trade_<i>_pnl`` columns.

V1 scope (per the brief's "frictionless V1"):
  - mid-price pricing convention only
  - no transaction costs, no bid/ask, no borrow availability
  - no financing assumption (financing is a Phase 1 PR 13 concern)
"""

from pathlib import Path

from shared.operators.evaluate_trades.operator import evaluate_trades
from shared.operators.evaluate_trades.schemas import EvaluateTradesParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "evaluate_trades",
    "EvaluateTradesParams",
    "CONFIG_PATH",
]
