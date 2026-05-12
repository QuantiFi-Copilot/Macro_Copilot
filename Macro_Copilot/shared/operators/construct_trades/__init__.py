"""shared.operators.construct_trades — EventSet → TradeSet.

Phase 1 PR 12.  The first operator in the backtest archetype's
chain.  Pure function: takes the (entry) ``EventSet`` plus a
holding rule + leg spec, emits a ``TradeSet`` whose ordered trades
describe entries at the event dates and exits per the configured
rule.

See ``operator.py`` for the compute function and the V1 scope
restrictions (fixed_horizon holding rule only; signal_exit /
stop_loss raise ``NotImplementedError`` with a pointer to
``planned_extensions``).
"""

from pathlib import Path

from shared.operators.construct_trades.operator import construct_trades
from shared.operators.construct_trades.schemas import (
    ConstructTradesParams,
    LegSpecInput,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "construct_trades",
    "ConstructTradesParams",
    "LegSpecInput",
    "CONFIG_PATH",
]
