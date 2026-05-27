"""fx_agent.forwards.tools.fx_basis_panel — cross-sectional FX CIP basis Panel.

Phase F1 (2026-05-27).

Folder-per-tool layout (composition primitive — see compute.py for
why this one is NOT a pure fetcher):

    fx_basis_panel/
      __init__.py     (this file — public-API re-exports)
      config.yaml     (conventions + methodology)
      schemas.py      (Pydantic input / output)
      compute.py      (deterministic Panel assembly via per-pair
                       basis math composition)

Purpose
-------
Phase F1 derived primitive: assemble a wide multi-pair ``Panel``
of cross-currency basis (CIP basis) values in BPS for the closed
V1 pair set ``{EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD}`` at one
tenor. Sign convention HARD-LOCKED to Bloomberg BCRX-style
(negative = USD scarcity) — inherited from cross_currency_basis
primitive (PR #239) which validated the sign convention via four
independent corroborations.

Asset-agnostic discipline
-------------------------
This tool is a thin FX-side composition layer. The per-pair
basis math (FX implied yield differential + OIS rate differential
+ subtraction in bps) is inlined here for self-containedness;
it duplicates the math validated in ``cross_currency_basis``.
Future refactor: extract ``_compute_pair_basis_bps_series`` to
``fx_agent/forwards/_shared.py`` once cross_currency_basis PR
lands, then both primitives consume the single helper.

Note for tests
--------------
Test seam for the OIS leg lives in ``compute.py`` via the imported
``fetch_cross_market_pair`` symbol; patch via
``...fx_basis_panel.compute.fetch_cross_market_pair``.
"""

from fx_agent.forwards.tools.fx_basis_panel.compute import (
    CONFIG_PATH,
    calculate_fx_basis_panel,
)
from fx_agent.forwards.tools.fx_basis_panel.schemas import (
    FXBasisPanelInput,
    FXBasisPanelOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_fx_basis_panel",
    "FXBasisPanelInput",
    "FXBasisPanelOutput",
]
