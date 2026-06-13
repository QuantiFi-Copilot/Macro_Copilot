"""swap_carry_and_roll — §7-C OIS carry/roll analytic primitive.

Stable re-exports.
"""

from rates_agent.ois.tools.swap_carry_and_roll.compute import (
    CONFIG_PATH,
    calculate_swap_carry_and_roll,
)
from rates_agent.ois.tools.swap_carry_and_roll.schemas import (
    SwapCarryAndRollCurrentMetrics,
    SwapCarryAndRollInput,
    SwapCarryAndRollOutput,
    SwapCarryAndRollTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_swap_carry_and_roll",
    "SwapCarryAndRollInput",
    "SwapCarryAndRollCurrentMetrics",
    "SwapCarryAndRollTimeSeriesRow",
    "SwapCarryAndRollOutput",
]
