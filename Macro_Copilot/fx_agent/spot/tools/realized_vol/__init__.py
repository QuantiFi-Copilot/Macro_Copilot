"""fx_agent.spot.tools.realized_vol — single-pair FX rolling realized vol.

Phase B follow-up (2026-05-25).
"""

from fx_agent.spot.tools.realized_vol.compute import (
    CONFIG_PATH,
    get_fx_realized_vol,
)
from fx_agent.spot.tools.realized_vol.schemas import (
    FXRealizedVolInput,
    FXRealizedVolMetrics,
    FXRealizedVolOutput,
)


__all__ = [
    "CONFIG_PATH",
    "get_fx_realized_vol",
    "FXRealizedVolInput",
    "FXRealizedVolMetrics",
    "FXRealizedVolOutput",
]
