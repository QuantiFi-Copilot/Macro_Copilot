"""fx_agent.spot.tools.fx_panel — cross-sectional FX spot Panel.

Phase B (2026-05-25).

Folder-per-tool layout (mirror of Sreeram's
``rates_agent.sovereign_bonds.tools.sovereign_yield_panel``):

    fx_panel/
      __init__.py     (this file — public-API re-exports)
      config.yaml     (conventions + methodology)
      schemas.py      (Pydantic input / output)
      compute.py      (deterministic Panel assembly, config-driven)

Purpose
-------
Cornerstone Phase B primitive: assemble a wide multi-instrument
``Panel`` of FX spot levels keyed by pair name (e.g. EURUSD, USDMXN),
for a market_scope subset of the universe (G10 / EM / G10_CROSSES /
ALL). Every later cross-sectional FX tool (returns series, drawdown,
realized vol, carry basket, correlation matrix, factor decomposition,
regime classifier) consumes a Panel produced by this primitive.

The tool surface lives in ``spot`` because the substrate is fx_spot
instruments. The SQL + Panel-construction logic lives partly in
``shared/analytics/fx_fetch.py`` (the asset-aware loader) so a sibling
forwards-panel / NDF-panel primitive (future PRs) can share the same
backend pattern.

Note for tests
--------------
Test seams (``fetch_fx_spot_panel``) live in ``compute.py``'s
namespace only; tests patch via
``...fx_panel.compute.fetch_fx_spot_panel``.
"""

from fx_agent.spot.tools.fx_panel.compute import (
    CONFIG_PATH,
    calculate_fx_panel,
)
from fx_agent.spot.tools.fx_panel.schemas import (
    FXPanelInput,
    FXPanelOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_fx_panel",
    "FXPanelInput",
    "FXPanelOutput",
]
