"""fx_agent.spot.tools.returns_series — single-pair FX log-returns series.

Phase B follow-up (2026-05-25).

Folder-per-tool layout (mirror of yield_levels):

    returns_series/
      __init__.py     (this file — public-API re-exports)
      config.yaml     (conventions + methodology)
      schemas.py      (Pydantic input / metrics / output)
      compute.py      (deterministic compute, config-driven)
"""

from fx_agent.spot.tools.returns_series.compute import (
    CONFIG_PATH,
    get_fx_returns_series,
)
from fx_agent.spot.tools.returns_series.schemas import (
    FXReturnsSeriesInput,
    FXReturnsSeriesMetrics,
    FXReturnsSeriesOutput,
)


__all__ = [
    "CONFIG_PATH",
    "get_fx_returns_series",
    "FXReturnsSeriesInput",
    "FXReturnsSeriesMetrics",
    "FXReturnsSeriesOutput",
]
