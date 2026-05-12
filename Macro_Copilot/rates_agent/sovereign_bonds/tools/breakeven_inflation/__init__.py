"""rates_agent.sovereign_bonds.tools.breakeven_inflation — TIPS-Nominal spread primitive.

Phase 1 PR 19.

The TIPS-vs-Nominal analog to ``calculate_swap_spread_tool``.  Both
legs are sovereign-family instruments (UST nominal + USD_TIPS real),
so the tool lives in the sovereign agent — no cross-domain
coordination needed.

Conventions
-----------
V1 implements ``nominal_breakeven`` convention only:
  breakeven_bps = (nominal_yield_pct - real_yield_pct) × 100

The ``inflation_swap_breakeven`` convention is declared in the YAML
enum (V2 surface stable) but raises NotImplementedError until we
ingest inflation-swap data with seasonal CPI adjustments.

Folder-per-tool layout:

    breakeven_inflation/
      __init__.py     (this file — public-API re-exports)
      config.yaml     (conventions + methodology)
      schemas.py      (Pydantic input / output)
      compute.py      (deterministic math, config-driven)
"""

from rates_agent.sovereign_bonds.tools.breakeven_inflation.compute import (
    CONFIG_PATH,
    calculate_breakeven_inflation,
)
from rates_agent.sovereign_bonds.tools.breakeven_inflation.schemas import (
    BreakevenInflationCurrentMetrics,
    BreakevenInflationInput,
    BreakevenInflationOutput,
    BreakevenInflationTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_breakeven_inflation",
    "BreakevenInflationCurrentMetrics",
    "BreakevenInflationInput",
    "BreakevenInflationOutput",
    "BreakevenInflationTimeSeriesRow",
]
