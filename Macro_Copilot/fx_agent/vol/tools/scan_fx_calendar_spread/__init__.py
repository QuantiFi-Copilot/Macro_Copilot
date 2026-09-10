"""fx_agent.vol.tools.scan_fx_calendar_spread — vol calendar spread scanner.

Phase F1 (2026-05-27).

Cross-sectional ATM vol calendar-spread ranker. Composes the
fx_vol_panel artifact at TWO tenors (front, back) and ranks
pairs by current (front - back) ATM vol spread.

Pure composition primitive — does NOT duplicate vol math
(that lives in fx_vol_panel + the per-pair vol_calendar_spread
primitive).
"""

from fx_agent.vol.tools.scan_fx_calendar_spread.compute import (
    CONFIG_PATH,
    run_fx_calendar_spread_scanner,
)
from fx_agent.vol.tools.scan_fx_calendar_spread.schemas import (
    FXCalendarSpreadScannerInput,
    FXCalendarSpreadScannerOutput,
    FXCalendarSpreadScannerRow,
)


__all__ = [
    "CONFIG_PATH",
    "run_fx_calendar_spread_scanner",
    "FXCalendarSpreadScannerInput",
    "FXCalendarSpreadScannerOutput",
    "FXCalendarSpreadScannerRow",
]
