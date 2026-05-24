"""fx_agent.spot.tools.drawdown — single-pair FX drawdown.

Phase B follow-up (2026-05-25).

Folder-per-tool layout (mirror of yield_levels / returns_series).
"""

from fx_agent.spot.tools.drawdown.compute import (
    CONFIG_PATH,
    calculate_fx_drawdown,
)
from fx_agent.spot.tools.drawdown.schemas import (
    FXDrawdownInput,
    FXDrawdownMetrics,
    FXDrawdownOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_fx_drawdown",
    "FXDrawdownInput",
    "FXDrawdownMetrics",
    "FXDrawdownOutput",
]
