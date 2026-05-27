"""fx_agent.forwards.tools.scan_fx_cross_currency_basis — basis scanner.

Phase F1 (2026-05-27).

Cross-sectional CIP basis ranker. Consumes the fx_basis_panel
artifact and ranks the V1 closed pair set by current basis level,
absolute basis, or absolute z-score over a 252-day window.

Pure composition primitive — does NOT duplicate the basis math
(that lives in cross_currency_basis + fx_basis_panel).
"""

from fx_agent.forwards.tools.scan_fx_cross_currency_basis.compute import (
    CONFIG_PATH,
    run_fx_cross_currency_basis_scanner,
)
from fx_agent.forwards.tools.scan_fx_cross_currency_basis.schemas import (
    FXBasisScannerInput,
    FXBasisScannerOutput,
    FXBasisScannerRow,
)


__all__ = [
    "CONFIG_PATH",
    "run_fx_cross_currency_basis_scanner",
    "FXBasisScannerInput",
    "FXBasisScannerOutput",
    "FXBasisScannerRow",
]
