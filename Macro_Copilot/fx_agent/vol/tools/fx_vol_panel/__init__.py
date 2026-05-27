"""fx_agent.vol.tools.fx_vol_panel — cross-sectional FX vol Panel.

Phase F1 (2026-05-27).

Folder-per-tool layout (mirror of
``fx_agent.spot.tools.fx_panel`` for the fx_vol substrate):

    fx_vol_panel/
      __init__.py     (this file — public-API re-exports)
      config.yaml     (conventions + methodology)
      schemas.py      (Pydantic input / output)
      compute.py      (deterministic Panel assembly, config-driven)

Purpose
-------
Phase F1 primitive: assemble a wide multi-pair ``Panel`` of FX
implied vols keyed by pair name (e.g. EURUSD, USDJPY) for a
(market_scope, tenor, smile_point) slice. ATM smile_point routes
to the ``fx_vol`` substrate; 25R/25B/10R/10B routes to
``fx_vol_smile``. The fetcher (``shared.analytics.fx_fetch.
fetch_fx_vol_panel``) handles the routing — callers don't need
to know the substrate split.

Asset-agnostic discipline
-------------------------
This tool is a pure fetcher+pivot+package primitive. It does NOT
compute correlation / PCA / factor loadings / regression — those
asset-agnostic operators live in ``shared/operators`` and consume
the returned ``Panel`` artifact directly.

Note for tests
--------------
Test seam (``fetch_fx_vol_panel``) lives in ``compute.py``'s
namespace only; tests patch via
``...fx_vol_panel.compute.fetch_fx_vol_panel``.
"""

from fx_agent.vol.tools.fx_vol_panel.compute import (
    CONFIG_PATH,
    calculate_fx_vol_panel,
)
from fx_agent.vol.tools.fx_vol_panel.schemas import (
    FXVolPanelInput,
    FXVolPanelOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_fx_vol_panel",
    "FXVolPanelInput",
    "FXVolPanelOutput",
]
