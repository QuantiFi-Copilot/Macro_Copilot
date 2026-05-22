"""rates_agent.sovereign_bonds.tools.sovereign_yield_panel — multi-leg yield Panel.

Phase 1 PR 19.

Folder-per-tool layout:

    sovereign_yield_panel/
      __init__.py     (this file — public-API re-exports)
      config.yaml     (conventions + methodology)
      schemas.py      (Pydantic input / output)
      compute.py      (deterministic Panel assembly, config-driven)

Purpose
-------
Assemble a wide multi-instrument ``Panel`` of sovereign yields keyed
by ``<curve_family>_<tenor>``, ready for consumption by the backtest
workflow's ``evaluate_trades`` operator.

The tool surface lives in ``sovereign_bonds`` because every leg in
the panel is a sovereign-family instrument (UST, USD_TIPS, DE_BUND,
UK_GILT, FR_OAT, IT_BTP, ES_BONO, JGB, CANADA_GOVT, AU_GOVT).  The
SQL + Panel-construction logic lives in
``shared/analytics/panel_assembly.py`` so a sibling OIS-only
``ois_rate_panel`` tool (future PR) can share the same backend.

Note for tests
--------------
Test seams (``fetch_instrument_panel``, ``date``) live in
``compute.py``'s namespace only; tests patch via
``...sovereign_yield_panel.compute.X``.
"""

from rates_agent.sovereign_bonds.tools.sovereign_yield_panel.compute import (
    CONFIG_PATH,
    build_sovereign_yield_panel,
)
from rates_agent.sovereign_bonds.tools.sovereign_yield_panel.schemas import (
    SovereignYieldPanelInput,
    SovereignYieldPanelLegSpec,
    SovereignYieldPanelOutput,
)


__all__ = [
    "CONFIG_PATH",
    "build_sovereign_yield_panel",
    "SovereignYieldPanelInput",
    "SovereignYieldPanelLegSpec",
    "SovereignYieldPanelOutput",
]
