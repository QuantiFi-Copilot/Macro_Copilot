"""fx_agent.vol.tools.scan_fx_vol_skew — vol skew (risk reversal) scanner.

Phase F1 (2026-05-27).

Cross-sectional FX vol skew ranker. Composes fx_vol_panel at
the requested smile_point ('25R', '10R', '25B', '10B') and
ranks pairs by current skew level / |skew| / |z-score|.

For risk-reversal smile_points (25R, 10R), POSITIVE skew = calls
priced above puts (USD-strong bias for xxxUSD pairs); NEGATIVE
skew = puts priced above calls. For butterfly smile_points
(25B, 10B), values are smile-wings vs ATM in vol points.

Pure composition primitive — does NOT compute vol math.
"""

from fx_agent.vol.tools.scan_fx_vol_skew.compute import (
    CONFIG_PATH,
    run_fx_vol_skew_scanner,
)
from fx_agent.vol.tools.scan_fx_vol_skew.schemas import (
    FXVolSkewScannerInput,
    FXVolSkewScannerOutput,
    FXVolSkewScannerRow,
)


__all__ = [
    "CONFIG_PATH",
    "run_fx_vol_skew_scanner",
    "FXVolSkewScannerInput",
    "FXVolSkewScannerOutput",
    "FXVolSkewScannerRow",
]
