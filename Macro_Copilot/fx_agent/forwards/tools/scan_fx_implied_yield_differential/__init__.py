"""fx_agent.forwards.tools.scan_fx_implied_yield_differential — iyd scanner.

Phase F1 (2026-05-27).

Cross-sectional FX implied yield differential ranker. For each
pair in the market_scope universe, computes the FX-implied
(local minus USD) rate spread from joined spot+forward history,
then ranks the cross-section by signed iyd / |iyd| / |z-score|.

This is the carry leader-board: most-positive iyd = highest
local-vs-USD rate spread priced into the forward (typically
high-yielder pairs from PM lens).

Pure composition primitive — the math IS inlined here for
self-containedness because it depends on per-pair joined
(spot, forward) data that the panels don't currently expose
in a stacked form.
"""

from fx_agent.forwards.tools.scan_fx_implied_yield_differential.compute import (
    CONFIG_PATH,
    run_fx_implied_yield_differential_scanner,
)
from fx_agent.forwards.tools.scan_fx_implied_yield_differential.schemas import (
    FXIYDScannerInput,
    FXIYDScannerOutput,
    FXIYDScannerRow,
)


__all__ = [
    "CONFIG_PATH",
    "run_fx_implied_yield_differential_scanner",
    "FXIYDScannerInput",
    "FXIYDScannerOutput",
    "FXIYDScannerRow",
]
